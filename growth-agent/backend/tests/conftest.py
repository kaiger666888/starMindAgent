"""conftest：保证 `app` 包可导入 + 公共桩夹具。"""
import os
import sys

# 让 backend/ 在 sys.path 上（从 backend/ 运行 pytest 时通常已就位，兜底）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # backend/
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from app.harness.inference_client import StubInferenceClient, _Script
from app.harness.circuit_breaker import (
    BreakerConfig, CircuitBreakerRegistry, ResilientCaller, RetryPolicy,
)
from app.harness.manager import InferenceSessionManager
from app.harness.models import HarnessTimeouts
from app.harness.reannotation import InMemoryTaskStore, WorkerPool
from app.harness.store import InMemoryCheckpointStore
from app.schemas import ConceptBlock, ConceptItem


@pytest.fixture
def fast_timeouts():
    """测试用极短超时，避免真实等待。"""
    return HarnessTimeouts(first_token_s=0.3, overall_s=1.0, json_s=0.5)


@pytest.fixture
def fast_breaker_config():
    return BreakerConfig(failure_threshold=2, error_rate_threshold=0.5,
                         min_samples_for_rate=2, recovery_seconds=0.2)


@pytest.fixture
def block():
    return ConceptBlock(
        concepts=[ConceptItem(name="梯度下降", aliases=["GD"], confidence=0.9)],
        model="stub",
    )


def make_manager(client, *, timeouts=None, breaker_config=None, failover_map=None,
                 reannotation_queue=None):
    timeouts = timeouts or HarnessTimeouts(first_token_s=0.3, overall_s=1.0, json_s=0.5)
    reg = CircuitBreakerRegistry(breaker_config or BreakerConfig(failure_threshold=2, min_samples_for_rate=2, recovery_seconds=0.2))
    caller = ResilientCaller(reg, failover_map)
    return InferenceSessionManager(
        client=client, caller=caller, retry=RetryPolicy(), timeouts=timeouts,
        checkpoint_store=InMemoryCheckpointStore(), reannotation_queue=reannotation_queue,
    )


def make_script(**kw):
    return _Script(**kw)
