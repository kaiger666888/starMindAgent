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
from app.api import qa_router, concept_router, harness_router, memory_router
from app.inference import backfill_queue, backfill_processor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    yield
    # 关闭：优雅停机
    if h.pool:
        await h.pool.stop()


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
                      "/memory/users/{user_id}/profile/refresh"],
    }
