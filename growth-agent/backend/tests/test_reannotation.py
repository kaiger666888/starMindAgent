"""异步补标注 worker：重试3->dead / 孤儿回收 / 归一化串行化（设计规格 §五）。"""
import asyncio

import pytest

from app.harness.reannotation import (
    InMemoryTaskStore, ReannotationTask, TaskKind, TaskStatus, WorkerPool,
)


async def _wait_idle(store, timeout=2.0):
    """等所有任务脱离 pending/running。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        st = await store.stats()
        if st.get("pending", 0) == 0 and st.get("running", 0) == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("tasks did not go idle: %s" % (await store.stats(),))


def _pool(store, backfill, norm, qa_exists, on_refresh=None, worker_count=2):
    return WorkerPool(
        store=store, backfill_handler=backfill, normalization_handler=norm,
        qa_exists_fn=qa_exists, on_refresh=on_refresh, worker_count=worker_count,
        poll_interval=0.001,
    )


@pytest.mark.asyncio
async def test_reannotation_success_backfills_and_refreshes():
    store = InMemoryTaskStore()
    refreshed = []

    async def backfill(task):
        return {"concept_ids": ["c1", "c2"]}

    async def norm(task):
        return {}

    async def qa_exists(qa_id):
        return True

    async def on_refresh(qa_id, cids):
        refreshed.append((qa_id, cids))

    pool = _pool(store, backfill, norm, qa_exists, on_refresh)
    pool.start()
    await pool.enqueue_reannotation("qa1", "s1", "正文快照", "L1")
    await _wait_idle(store)
    await pool.stop()

    st = await store.stats()
    assert st.get("done") == 1
    assert refreshed == [("qa1", ["c1", "c2"])]
    assert pool.stats()["completion_rate"] == 1.0


@pytest.mark.asyncio
async def test_reannotation_retry3_then_dead():
    # max_retry=3：直接配置 store（不再 monkeypatch frozen settings）
    store = InMemoryTaskStore(max_retry=3)
    attempts = {"n": 0}

    async def backfill(task):
        attempts["n"] += 1
        raise RuntimeError("extract failed")

    async def norm(task):
        return {}

    async def qa_exists(qa_id):
        return True

    pool = _pool(store, backfill, norm, qa_exists, on_refresh=None)
    pool.start()
    await pool.enqueue_reannotation("qa2", "s1", "正文", "L1")
    await _wait_idle(store, timeout=3.0)
    await pool.stop()

    st = await store.stats()
    assert st.get("dead") == 1
    assert attempts["n"] == 3  # 重试 3 次
    tasks = [t for t in store.all_tasks() if t.status == TaskStatus.DEAD.value]
    assert tasks and tasks[0].retry_count == 3
    assert pool.stats()["dead_letter_rate"] > 0


@pytest.mark.asyncio
async def test_orphan_reannotation_reclaimed():
    store = InMemoryTaskStore()

    async def backfill(task):
        raise AssertionError("should not backfill orphan")

    async def norm(task):
        return {}

    async def qa_exists(qa_id):
        return False  # QAStep 已删除 -> 孤儿

    pool = _pool(store, backfill, norm, qa_exists)
    pool.start()
    await pool.enqueue_reannotation("ghost", "s1", "正文", "L1")
    await _wait_idle(store)
    await pool.stop()

    st = await store.stats()
    assert st.get("reclaimed") == 1
    tasks = [t for t in store.all_tasks() if t.status == TaskStatus.RECLAIMED.value]
    assert tasks and "orphan" in (tasks[0].last_error or "")


@pytest.mark.asyncio
async def test_orphan_reclaim_via_store_sweep():
    """store.reclaim_orphans 批量回收孤儿（设计规格 §五 孤儿任务直接回收）。"""
    store = InMemoryTaskStore()
    await store.enqueue(ReannotationTask(qa_id="gone", session_id="s1",
                                         kind=TaskKind.REANNOTATION.value,
                                         payload={"answer_snapshot": "x"}))
    await store.enqueue(ReannotationTask(qa_id="alive", session_id="s1",
                                         kind=TaskKind.REANNOTATION.value,
                                         payload={"answer_snapshot": "x"}))

    async def qa_exists(qa_id):
        return qa_id == "alive"

    n = await store.reclaim_orphans(qa_exists)
    assert n == 1
    tasks = {t.qa_id: t for t in store.all_tasks()}
    assert tasks["gone"].status == TaskStatus.RECLAIMED.value
    assert tasks["alive"].status == TaskStatus.PENDING.value


@pytest.mark.asyncio
async def test_normalization_serialized_per_session():
    """归一化按 session 维度强制串行化（架构文档 §3.2 / §7.4）。"""
    store = InMemoryTaskStore()
    active = {"cur": 0, "max": 0}
    order = []

    async def norm(task):
        active["cur"] += 1
        active["max"] = max(active["max"], active["cur"])
        order.append(task.payload["seq"])
        await asyncio.sleep(0.03)
        active["cur"] -= 1
        return {}

    async def backfill(task):
        return {"concept_ids": []}

    async def qa_exists(qa_id):
        return True

    # 3 个归一化任务同一 session；2 worker，若无串行则可能并发
    pool = _pool(store, backfill, norm, qa_exists, worker_count=2)
    pool.start()
    for i in range(3):
        await pool.enqueue_normalization("s1", {"action": "merge", "seq": i, "id_a": "a", "id_b": "b"})
    await _wait_idle(store, timeout=3.0)
    await pool.stop()

    assert active["max"] == 1  # 同 session 严格串行，无并发
    assert order == [0, 1, 2]  # 顺序执行
    st = await store.stats()
    assert st.get("done") == 3


@pytest.mark.asyncio
async def test_normalization_different_sessions_parallel():
    """不同 session 的归一化可并行（串行化仅按 session 维度）。"""
    store = InMemoryTaskStore()
    active = {"cur": 0, "max": 0}

    async def norm(task):
        active["cur"] += 1
        active["max"] = max(active["max"], active["cur"])
        await asyncio.sleep(0.03)
        active["cur"] -= 1
        return {}

    async def backfill(task):
        return {"concept_ids": []}

    async def qa_exists(qa_id):
        return True

    pool = _pool(store, backfill, norm, qa_exists, worker_count=3)
    pool.start()
    await pool.enqueue_normalization("sA", {"action": "merge", "id_a": "a", "id_b": "b"})
    await pool.enqueue_normalization("sB", {"action": "merge", "id_a": "a", "id_b": "b"})
    await pool.enqueue_normalization("sC", {"action": "merge", "id_a": "a", "id_b": "b"})
    await _wait_idle(store, timeout=3.0)
    await pool.stop()

    assert active["max"] >= 2  # 不同 session 并行


@pytest.mark.asyncio
async def test_reset_running_on_startup():
    """worker 重启：stale running 任务重置为 pending 恢复消费。"""
    store = InMemoryTaskStore()
    t = ReannotationTask(qa_id="qa", session_id="s", kind=TaskKind.REANNOTATION.value,
                         payload={"answer_snapshot": "x"}, status=TaskStatus.RUNNING.value)
    await store.enqueue(t)
    # 手动改回 running（enqueue 不改 status）
    t.status = TaskStatus.RUNNING.value
    n = await store.reset_running()
    assert n == 1
    assert t.status == TaskStatus.PENDING.value
