# Testing Patterns

**Analysis Date:** 2026-08-11

## Test Framework

**Runner:**
- pytest >=8.0 (Python backend)
- Config: `growth-agent/backend/pytest.ini`
  ```ini
  [pytest]
  asyncio_mode = auto
  testpaths = tests
  filterwarnings =
      ignore::DeprecationWarning
  ```
- No frontend test framework configured (no jest/vitest in `package.json` devDependencies)

**Assertion Library:**
- Python: built-in `assert` statements + `pytest.raises` for exception testing
- No third-party assertion library (no `assertpy`, `expects`, etc.)

**Run Commands:**
```bash
cd growth-agent/backend && pytest          # Run all tests
cd growth-agent/backend && pytest -v      # Verbose mode
cd growth-agent/backend && pytest tests/test_qastep.py   # Single file
cd growth-agent/backend && pytest -k "test_l0"           # By name pattern
cd growth-agent/backend && pytest --tb=short             # Short tracebacks
```

**Async test support:**
- `pytest-asyncio>=0.23` with `asyncio_mode = auto` — async tests run without `@pytest.mark.asyncio` strictly needed, but most tests use it explicitly
- Tests use `@pytest.mark.asyncio` decorator on async test functions

## Test File Organization

**Location:**
- Backend tests: `growth-agent/backend/tests/` — separate `tests/` directory alongside `app/`
- Subdirectories mirror `app/` structure: `tests/harness/`, `tests/inference/`
- Eval tests: directory exists at `growth-agent/eval/tests/` but is empty (no test files)

**Naming:**
- `test_*.py` for test modules — e.g., `test_qastep.py`, `test_concept_service.py`, `test_circuit_breaker.py`
- `test_*` for test functions — e.g., `test_legal_transitions()`, `test_l0_happy_path()`
- Chinese descriptions in docstrings, English function names

**Structure:**
```
growth-agent/backend/tests/
├── conftest.py                    # Shared fixtures + sys.path setup
├── __init__.py
├── test_qastep.py                 # State machine + StreamSplitter logic
├── test_concept_service.py        # Concept service contract tests
├── test_normalization.py          # Normalization pipeline logic
├── test_inference_session.py       # InferenceSession L0/L1/timeout/retry
├── test_circuit_breaker.py        # Breaker state transitions
├── test_sentinel.py               # Sentinel cross-chunk detection
├── test_checkpoint_recovery.py    # Checkpoint save/resume/reconnect
├── test_observability.py          # /harness/obs/metrics endpoint
├── test_reannotation.py           # Async backfill worker lifecycle
├── test_integration.py            # End-to-end QAStepPipeline + harness
├── harness/
│   ├── __init__.py
│   ├── test_checkpoint.py         # Checkpoint save/resume/unknown
│   ├── test_circuit_breaker.py    # Breaker open/half-open/failover
│   ├── test_observability.py      # Metrics fields + gates
│   └── test_reannotation.py       # Worker retry/orphan/latency
└── inference/
    ├── __init__.py
    ├── test_client.py             # InferenceClient L0-L3 degradation
    └── test_sentinel.py            # Sentinel cross-chunk any position
```

## Test Structure

**Suite Organization:**
```python
"""纯逻辑测试：QAStep 状态机迁移合法性、StreamSplitter sentinel 检测。

不依赖数据库，可离线运行：pytest backend/tests/test_qastep.py
"""
import json
import pytest
from app.qastep.state_machine import (
    QAStatus, QAStepRuntime, IllegalTransition, QATransition,
)
from app.inference.protocol import StreamSplitter, SENTINEL
from app.schemas import ConceptBlock, ConceptItem


def test_legal_transitions():
    rt = QAStepRuntime("qa1", "s1", "q")
    # generating -> extracting -> waiting 合法
    rt.assert_transition(QAStatus.GENERATING, QAStatus.EXTRACTING)
    rt.assert_transition(QAStatus.EXTRACTING, QAStatus.WAITING)
    # 三个出口从 waiting 出发
    rt.assert_transition(QAStatus.WAITING, QAStatus.GENERATING)


def test_illegal_transition():
    rt = QAStepRuntime("qa1", "s1", "q")
    with pytest.raises(IllegalTransition):
        rt.assert_transition(QAStatus.WAITING, QAStatus.EXTRACTING)
```
Pattern: `growth-agent/backend/tests/test_qastep.py`

**Patterns:**
- Module docstring describes what's tested + whether DB is needed
- One `test_*` function per behavior case
- Arrange-Act-Assert structure (no `given`/`when`/`then` comments)
- Chinese inline comments for business context
- `pytest.raises(ExceptionType)` for expected exceptions
- No `setup`/`teardown` methods — fixtures used instead

## Mocking

**Framework:** No mocking library (no `unittest.mock`, `pytest-mock`, etc.). Uses **hand-written stub/fake classes**.

**Patterns:**

Stub implementations co-located with production code:
```python
# In production module: growth-agent/backend/app/inference/protocol.py
class StubInferenceSession:
    """模拟一次成功的 L0 调用：先逐段吐正文，再吐 sentinel + ConceptBlock。"""
    async def stream(self) -> AsyncIterator[dict]:
        for chunk in self._answer_chunks:
            yield {"kind": "delta", "text": chunk}
        yield {"kind": "sentinel"}
        yield {"kind": "json_done", "block": self._concept_block}

class FailingInferenceSession:
    """模拟 L1 降级：正文流出后 JSON 解析失败。"""
    async def stream(self):
        yield {"kind": "delta", "text": "部分正文已渲染……"}
        yield {"kind": "sentinel"}
        yield {"kind": "error", "message": "json parse failed (L1)"}
```

Test-local fakes for specific scenarios:
```python
# In test file: growth-agent/backend/tests/test_inference_session.py
class FirstTokenTimeoutThenSuccess:
    """第 1 次流首 token 超时，第 2 次（重试）正常。"""
    def __init__(self, block, first_token_s):
        self.block = block
        self.first_token_s = first_token_s
        self.calls = 0

    async def stream(self, req):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(self.first_token_s + 0.2)  # 超时
            yield {"delta": "never"}
            return
        yield StreamChunk(call_id=f"c{self.calls}", delta="重试后正文")
        # ...

class FakeQueue:
    """In-memory fake for reannotation queue."""
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, qa_id, session_id, answer_snapshot, reason="L1"):
        self.enqueued.append({"qa_id": qa_id, ...})
```

Test-local fake repository:
```python
# growth-agent/backend/tests/test_integration.py
class FakeRepo:
    """不依赖 DB 的 repository 桩，记录状态迁移与埋点。"""
    def __init__(self):
        self.status = QAStatus.GENERATING
        self.answer = ""
        self.telemetry = None

    async def transition(self, qa_id, nxt):
        self.status = nxt

    async def append_answer(self, qa_id, delta):
        self.answer += delta
    # ...
```

**What to Mock:**
- LLM inference backends (use `StubLLMBackend`, `StubInferenceClient`, `StubInferenceSession`)
- Database (use `FakeRepo`, `InMemoryCheckpointStore`, `InMemoryTaskStore`)
- External queues (use `FakeQueue`)
- Time-sensitive operations (use `fast_timeouts` fixture with 0.3s/1.0s/0.5s)

**What NOT to Mock:**
- State machine logic (`QAStepRuntime`, `QAStatus`) — tested directly
- `StreamSplitter`/`SentinelDetector` — pure logic, tested with real chunking
- Schemas/Pydantic models — validated with real construction
- `CircuitBreaker` state transitions — tested with real breaker instances

## Fixtures and Factories

**Test Data:**
```python
# growth-agent/backend/tests/conftest.py
@pytest.fixture
def block():
    return ConceptBlock(
        concepts=[ConceptItem(name="梯度下降", aliases=["GD"], confidence=0.9)],
        model="stub",
    )

@pytest.fixture
def fast_timeouts():
    """测试用极短超时，避免真实等待。"""
    return HarnessTimeouts(first_token_s=0.3, overall_s=1.0, json_s=0.5)

@pytest.fixture
def fast_breaker_config():
    return BreakerConfig(failure_threshold=2, error_rate_threshold=0.5,
                         min_samples_for_rate=2, recovery_seconds=0.2)
```

**Factory functions (not fixtures):**
```python
# growth-agent/backend/tests/conftest.py
def make_manager(client, *, timeouts=None, breaker_config=None, failover_map=None,
                 reannotation_queue=None):
    """Build a fully-wired InferenceSessionManager for tests."""
    timeouts = timeouts or HarnessTimeouts(first_token_s=0.3, overall_s=1.0, json_s=0.5)
    reg = CircuitBreakerRegistry(breaker_config or BreakerConfig(...))
    caller = ResilientCaller(reg, failover_map)
    return InferenceSessionManager(
        client=client, caller=caller, retry=RetryPolicy(), timeouts=timeouts,
        checkpoint_store=InMemoryCheckpointStore(), reannotation_queue=reannotation_queue,
    )

def make_script(**kw):
    return _Script(**kw)
```
- Factory functions imported directly: `from tests.conftest import make_manager, make_script` — `growth-agent/backend/tests/test_inference_session.py:20`

**Location:**
- Shared fixtures: `growth-agent/backend/tests/conftest.py`
- Test-local fakes: inline in each test file (not shared)
- Sys.path setup: `conftest.py` ensures `backend/` is on `sys.path` — `growth-agent/backend/tests/conftest.py:6-9`

## Coverage

**Requirements:** None enforced (no `--cov` flag, no coverage config, no coverage threshold)

**View Coverage:**
```bash
cd growth-agent/backend && pytest --cov=app --cov-report=html  # If pytest-cov installed
```

## Test Types

**Unit Tests:**
- Pure logic tests: state machine transitions, sentinel detection, color tier mapping, threshold validation
- No DB, no async, no I/O — `growth-agent/backend/tests/test_qastep.py`, `growth-agent/backend/tests/test_normalization.py`
- Circuit breaker state machine: `growth-agent/backend/tests/test_circuit_breaker.py`, `growth-agent/backend/tests/harness/test_circuit_breaker.py`
- Run fast (<1s total)

**Integration Tests:**
- Async tests with stub backends, in-memory stores, fake repos
- Test full pipelines: `QAStepPipeline.run()` + `InferenceSession` — `growth-agent/backend/tests/test_integration.py`
- Test `InferenceSessionManager` lifecycle: start/abort/resume — `growth-agent/backend/tests/test_inference_session.py`
- Test checkpoint recovery + reconnect — `growth-agent/backend/tests/test_checkpoint_recovery.py`
- Test async worker pool lifecycle — `growth-agent/backend/tests/test_reannotation.py`
- Use `@pytest.mark.asyncio` with `asyncio_mode = auto`

**API/Route Tests:**
- FastAPI `TestClient` for endpoint testing — `growth-agent/backend/tests/test_observability.py:57-67`
  ```python
  app = FastAPI()
  app.include_router(observability_router(metrics))
  client = TestClient(app)
  r = client.get("/harness/obs/metrics")
  assert r.status_code == 200
  ```

**E2E Tests:**
- Not used (no Playwright, no frontend E2E framework configured)

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_l0_happy_path(block):
    client = StubInferenceClient(default=make_script(
        answer_chunks=["关于X，", "核心在Y。"], concept_block=block))
    mgr = make_manager(client)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)  # collect all events from async generator
    kinds = [e["kind"] for e in events]
    assert "delta" in kinds
    assert kinds[-1] == "json_done"
```
Pattern: `growth-agent/backend/tests/test_inference_session.py:99`

Async event draining helper:
```python
async def _drain(sess):
    """Consume all events from an async generator into a list."""
    events = []
    async for ev in sess.stream():
        events.append(ev)
    return events
```
Pattern: `growth-agent/backend/tests/test_inference_session.py:91`

**Error/Exception Testing:**
```python
def test_illegal_transition():
    rt = QAStepRuntime("qa1", "s1", "q")
    with pytest.raises(IllegalTransition):
        rt.assert_transition(QAStatus.WAITING, QAStatus.EXTRACTING)
```
Pattern: `growth-agent/backend/tests/test_qastep.py:23`

**Parametrized Testing:**
```python
@pytest.mark.parametrize("step", [1, 2, 3, 5, 7, 11, 13])
def test_sentinel_cross_chunk_any_position(step):
    """sentinel 在 chunk 边界任意位置切断都能正确检测。"""
    # ...
    assert saw, f"step={step} 未检测到 sentinel"
```
Pattern: `growth-agent/backend/tests/inference/test_sentinel.py:12`

**Monkeypatch for environment:**
```python
@pytest.mark.asyncio
async def test_e2e_l0_with_harness_session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.qastep.telemetry.TELEMETRY_DIR", str(tmp_path))
    # ...
```
Pattern: `growth-agent/backend/tests/test_integration.py:54`

**Polling for async completion:**
```python
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
```
Pattern: `growth-agent/backend/tests/test_reannotation.py:11`

**Test docstring as spec:**
```python
"""InferenceSession 生产封装：L0/L1/超时/重试/降级（设计规格 §二 §四 / 协议 §5）。

覆盖路径：
- L0 正常：delta + sentinel + json_done
- L1 JSON 失败 -> 结构化重试(extract_only)恢复 -> json_done
- L1 JSON 失败 + 结构化重试失败 -> error(L1) + 补标注入队
- L1 无 sentinel：正文完整无结构化 -> 补标注入队（不报 error）
- 首 token 超时重试一次成功 / 重试仍失败 -> L1
- 整体超时：正文已开始 -> 只降级不重试
- 正文已渲染后流异常 -> 只降级不重试
- abort：用户回上层 -> 落盘保留现场
"""
```
Pattern: `growth-agent/backend/tests/test_inference_session.py:1`

**Gate/assertion alignment with spec:**
Tests assert that metric gates match architecture doc values:
```python
assert m["interruption_recovery"]["gate"] == "> 95%"
assert m["circuit_breaker"]["alert_threshold"] == "> 10%/h"
assert m["async_reannotation"]["gate"] == "completion > 90%, dead_letter < 2%"
assert m["backfill_latency"]["gate"] == "< 30000 ms"
```
Pattern: `growth-agent/backend/tests/test_observability.py:48-54`

---

*Testing analysis: 2026-08-11*
