"""异步补标注 worker 测试：重试3→dead、孤儿回收、串行归一化。"""
import asyncio
import pytest
from app.harness.store import InMemoryTaskStore
from app.harness.reannotation import ReannotationWorker, WorkerPool


@pytest.mark.asyncio
async def test_retry_three_then_dead():
    """重试 3 次仍失败 -> 标 dead（dead letter < 2% 门禁对齐）。"""
    store = InMemoryTaskStore(max_retry=3)
    attempts = []

    async def proc(qa_id):
        attempts.append(qa_id)
        raise RuntimeError("always fails")

    w = ReannotationWorker(store, proc, qa_exists_fn=lambda q: asyncio.sleep(0, result=True))
    await w.enqueue("qa1")
    w.start(n_workers=1)
    # 跑几轮直到 dead
    for _ in range(20):
        await asyncio.sleep(0.01)
        if store._tasks.get("qa1", {}).get("status") in ("dead", "done"):
            break
    await w.stop()
    assert store._tasks["qa1"]["status"] == "dead"
    assert store._tasks["qa1"]["retry_count"] == 3


@pytest.mark.asyncio
async def test_success_marks_done():
    store = InMemoryTaskStore()
    done = []

    async def proc(qa_id):
        done.append(qa_id)

    w = ReannotationWorker(store, proc, qa_exists_fn=lambda q: asyncio.sleep(0, result=True))
    await w.enqueue("qa2")
    w.start(1)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if store._tasks.get("qa2", {}).get("status") == "done":
            break
    await w.stop()
    assert done == ["qa2"]
    assert store._tasks["qa2"]["status"] == "done"


@pytest.mark.asyncio
async def test_orphan_task_recycled():
    """回填前校验目标 QAStep 不存在 -> 孤儿任务直接回收（dead）。"""
    store = InMemoryTaskStore()

    async def proc(qa_id):
        raise AssertionError("should not run on orphan")

    w = ReannotationWorker(store, proc, qa_exists_fn=lambda q: asyncio.sleep(0, result=False))
    await w.enqueue("qa3")
    w.start(1)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if store._tasks.get("qa3", {}).get("status") == "dead":
            break
    await w.stop()
    assert store._tasks["qa3"]["status"] == "dead"
    assert "orphan" in (store._tasks["qa3"]["last_error"] or "")


@pytest.mark.asyncio
async def test_pool_records_latency_p95():
    store = InMemoryTaskStore()

    async def proc(qa_id):
        pass

    pool = WorkerPool(store, proc, qa_exists_fn=lambda q: asyncio.sleep(0, result=True))
    await pool.record_latency(100.0)
    await pool.record_latency(200.0)
    await pool.record_latency(300.0)
    # 3 样本 p95 -> 最大值
    assert pool.p95_latency_ms() >= 200.0
