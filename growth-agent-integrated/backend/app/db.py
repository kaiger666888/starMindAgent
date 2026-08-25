"""异步数据库会话 (SQLAlchemy 2.0 async)。

生产用 asyncpg；测试可替换为 sqlite。提供 get_session 依赖注入。
引擎懒加载：import 期不建连接，纯逻辑测试（不触库）无需 DB 驱动可用。
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.config import settings

_engine = None
_SessionLocal = None

_IS_SQLITE = settings.database_url.startswith("sqlite")


def is_sqlite() -> bool:
    """sqlite 本地开发模式标志（JSONB/UUID PG 方言需绕行）。"""
    return _IS_SQLITE


def _ensure():
    """首次访问时建 engine + sessionmaker（懒加载）。"""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
        _SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
        if _IS_SQLITE:
            # sqlite 外键约束默认关闭（归一化/undo 依赖 FK 语义）
            from sqlalchemy import event

            @event.listens_for(_engine.sync_engine, "connect")
            def _fk_on(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
    return _SessionLocal


def get_engine():
    """按需取 engine（init_db / 运维用）。"""
    _ensure()
    return _engine


def SessionLocal_factory():
    _ensure()
    return _SessionLocal


async def get_session() -> AsyncSession:
    """FastAPI 依赖注入。"""
    async with SessionLocal_factory()() as session:
        yield session


@asynccontextmanager
async def session_scope():
    """命令行 / worker 显式事务。"""
    maker = SessionLocal_factory()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """开发期便捷建表（生产走 alembic 迁移 migrations/*.sql）。"""
    _ensure()
    from app.models.tables import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
