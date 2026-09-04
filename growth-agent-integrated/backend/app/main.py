"""FastAPI 主应用。

后端框架：Python/FastAPI，LLM 调用走异步任务队列 + SSE 流式回推。
启动：uvicorn app.main:app --reload
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api import qa_router, concept_router, harness_router, memory_router, learning_router, cards_router
from app.inference import backfill_queue, backfill_processor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# uvicorn 的 LOGGING_CONFIG 会先接管 root（level=WARNING），basicConfig 不生效 -> 显式提级
logging.getLogger("app").setLevel(logging.INFO)
log = logging.getLogger("growth-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：harness 补标注 worker（持久化任务，DB 兜底恢复）
    from app.harness.app import get_harness
    h = get_harness()
    if h.pool:
        h.pool.start()
        log.info("harness backfill worker started")
    # 兼容旧 backfill_queue（主仓 tasks.py）
    backfill_queue.start_worker(backfill_processor)
    # 记忆卡片：周期扫描 checked QA 生成卡片（后台 worker，30 分钟一轮）
    from app.memory.cards import card_worker
    card_worker.start()
    log.info("memory card worker started")
    # 启动：seed 预置核心概念（需求五"预置+LLM补充"）
    try:
        from app.concept import concept_service
        cnt = await concept_service.seed_preset_concepts()
        if cnt:
            log.info("seeded %d preset concepts", cnt)
    except Exception as e:
        log.warning("seed preset failed: %s", e)
    yield
    # 关闭：优雅停机
    if h.pool:
        await h.pool.stop()
    await card_worker.stop()


app = FastAPI(
    title="「伴你成长」学习 Agent 后端",
    version="1.0.0",
    description="QAStep 状态机 + Concept 服务 + SSE 流式回推",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(qa_router)
app.include_router(concept_router)
app.include_router(harness_router)
app.include_router(memory_router)
app.include_router(learning_router)
app.include_router(cards_router)


@app.get("/health")
async def health():
    return {"status": "ok", "sentinel": settings.concept_sentinel}


@app.get("/")
async def root():
    return {
        "service": "growth-agent-backend",
        "endpoints": ["/qa/start", "/qa/{qa_id}/stream",
                      "/qa/{qa_id}/drilldown", "/qa/{qa_id}/rollback",
                      "/concept/merge", "/concept/undo",
                      "/concept/graph", "/concept/explore",
                      "/harness/obs/metrics",
                      "/memory/users/{user_id}/sessions",
                      "/memory/sessions/{session_id}",
                      "/memory/users/{user_id}/profile",
                      "/memory/users/{user_id}/profile/refresh",
                      "/memory/cards/users/{user_id}/due",
                      "/memory/cards/users/{user_id}/progress",
                      "/memory/cards/users/{user_id}/from-selection",
                      "/memory/cards/{card_id}/grade"],
    }
