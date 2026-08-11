# Coding Conventions

**Analysis Date:** 2026-08-11

## Naming Patterns

**Files:**
- Python: `snake_case.py` — e.g., `routes_concept.py`, `state_machine.py`, `circuit_breaker.py`
- React/JS: `PascalCase.jsx` for components, `camelCase.js` for modules — e.g., `ConceptGraph.jsx`, `TreeView.jsx`, `qaStore.js`, `client.js`
- Config files: lowercase with dot-prefix or plain — e.g., `pytest.ini`, `vite.config.js`, `.env.example`

**Functions:**
- Python: `snake_case` — e.g., `merge_concepts()`, `prompt_hash()`, `build_prompt()`, `replay_undo()`
- JS: `camelCase` — e.g., `startQA()`, `subscribeStream()`, `drillDown()`, `popToLayer()`
- Private/internal: leading underscore — e.g., `_ensure()`, `_safe_emit()`, `_color_tier()`, `_keyword_extract()`, `_telemetry_path()`

**Variables:**
- Python: `snake_case` — e.g., `session_id`, `qa_id`, `concept_block`, `answer_buf`
- JS: `camelCase` — e.g., `activeSessionId`, `inflight`, `question`
- Module-level constants: `UPPER_SNAKE_CASE` — e.g., `SENTINEL`, `CONTEXT_TOKEN_BUDGET`, `_SENTINEL_RE`, `TELEMETRY_DIR`

**Types/Classes:**
- Python: `PascalCase` — e.g., `QAStepPipeline`, `ConceptNormalizer`, `CircuitBreaker`, `InferenceClient`, `StreamSplitter`, `StubLLMBackend`
- Enums: `PascalCase` class, members `UPPER_SNAKE` — e.g., `QAStatus.GENERATING`, `BreakerState.OPEN`, `SessionStatus.STREAMING`
- React components: `PascalCase` — e.g., `TreeView`, `ConceptGraph`, `App`

## Code Style

**Formatting:**
- Python: No external formatter config detected (no `.flake8`, `pyproject.toml` with black/ruff, or `setup.cfg` found). Style is enforced by convention.
- JS: No `.prettierrc` or eslint config detected. Vite defaults apply.
- Indentation: 4 spaces (Python), 2 spaces (JS/JSX)
- Line length: ~100-120 chars (no hard limit configured)
- Trailing newlines: files end with a newline

**Linting:**
- Python: No linter config detected. `# noqa: BLE001` and `# noqa: F401` comments used inline to suppress specific checks.
- JS: No eslint/prettier config detected.

**Key Python style conventions (observed across codebase):**
- `from __future__ import annotations` at top of every module (PEP 563, enables `str | None` syntax)
- Module-level docstring (triple-quote, often in Chinese) explaining the module's purpose and referencing architecture doc sections
- Inline comments use `#` with Chinese text for business logic explanations
- Section dividers: `# ---------------------------------------------------------------------------`
- `log = logging.getLogger(__name__)` at module level

**Key JS style conventions:**
- ES modules (`"type": "module"` in `package.json`)
- No semicolons (semicolons omitted in `.jsx`/`.js` files)
- Single quotes for strings
- Inline style objects (no CSS modules/styled-components)
- Arrow functions for exports

## Import Organization

**Python import order:**
1. `from __future__ import annotations`
2. Standard library (`json`, `logging`, `os`, `re`, `hashlib`, `asyncio`, `dataclasses`, `enum`, `typing`, `pathlib`, `contextlib`)
3. Third-party (`fastapi`, `sqlalchemy`, `pydantic`)
4. Application (`app.config`, `app.db`, `app.models.tables`, `app.schemas`, `app.qastep`, `app.concept`, `app.inference`, `app.harness`)

**Path Aliases:**
- Python: Absolute imports from `app.` package root — e.g., `from app.config import settings`, `from app.db import session_scope`
- JS: Relative imports — e.g., `import { useStore } from '../store/qaStore'`, `import * as api from '../api/client'`

**Lazy imports (inline):** Used to break circular dependencies and defer heavy imports:
```python
# Inside function bodies:
from app.db import session_scope
from app.models.tables import QASession
from app.qastep.state_machine import QAStepRuntime
```
Pattern: `growth-agent/backend/app/api/routes_qa.py:36` — `from app.db import session_scope` inside `start()` handler.

## Error Handling

**Patterns:**
- Domain-specific exceptions: Custom exception classes per module
  - `IllegalTransition` — `growth-agent/backend/app/qastep/state_machine.py:70`
  - `OptimisticLockConflict` — `growth-agent/backend/app/qastep/state_machine.py:74`
  - `DepthLimitReached` — `growth-agent/backend/app/qastep/repository.py:187`
  - `CircuitOpenError` — `growth-agent/backend/app/harness/circuit_breaker.py:29`
- HTTP error mapping: FastAPI `HTTPException` with appropriate status codes
  - `400` for `ValueError` (bad request) — `growth-agent/backend/app/api/routes_concept.py:22`
  - `404` for not found — `growth-agent/backend/app/api/routes_concept.py:30`
  - `422` for business rule violations (depth limit) — `growth-agent/backend/app/api/routes_qa.py:81`
- Broad exception catching with `# noqa: BLE001` for non-blocking paths:
  ```python
  except Exception as e:  # noqa: BLE001
      log.exception("InferenceClient stream error qa_id=%s", self.qa_id)
  ```
  Pattern: `growth-agent/backend/app/inference/client.py:107`
- Graceful degradation: L0-L3 degradation chain in inference layer — failures degrade, never crash the main flow
  - `growth-agent/backend/app/inference/client.py:123` — `_degrade()` method
- Transaction rollback: `session_scope()` context manager auto-rolls back on exception
  - `growth-agent/backend/app/db.py:46-55`
- Telemetry never blocks: `emit_telemetry` catches all exceptions and only logs
  - `growth-agent/backend/app/qastep/telemetry.py:53`

**JS error handling:**
- Throw on HTTP failure: `if (!res.ok) throw new Error(\`start failed: ${res.status}\`)` — `growth-agent/frontend/src/api/client.js:12`
- SSE error callback: `es.onerror` handler delegates to `handlers._onerror` — `growth-agent/frontend/src/api/client.js:26`

## Logging

**Framework:** Python `logging` standard library

**Patterns:**
- Module-level logger: `log = logging.getLogger(__name__)` in every Python module
- Root config in entry point: `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")` — `growth-agent/backend/app/main.py:17`
- Log level usage:
  - `log.info()` — lifecycle events (worker started, thresholds reloaded)
  - `log.warning()` — degradation fallbacks, config load failures
  - `log.exception()` — caught exceptions with traceback (includes stack trace)
  - `log.debug()` — not heavily used
- Structured logging with context: `log.info("L1 backfill queued (no hook) qa_id=%s", self.qa_id)` — `growth-agent/backend/app/inference/client.py:222`
- No `print()` statements anywhere in the codebase
- No `console.log` in frontend source (only in API client error throws)

## Comments

**When to Comment:**
- Module docstrings: Every Python module has a docstring explaining its purpose, referencing architecture doc sections (e.g., "技术架构文档第三节", "设计规格 §4")
- Function docstrings: Public API methods and complex logic get docstrings
- Inline comments: Business rule explanations, degradation rationale, architecture references
- Section dividers: `# ---------------------------------------------------------------------------` used to group related methods

**JSDoc/TSDoc:**
- Python docstrings: Triple-quoted, often in Chinese, following Google-style loosely
  ```python
  async def run(self) -> AsyncIterator[dict]:
      """流式产出事件，供 SSE 推给前端。

      事件类型：
        {type: 'answer_delta', text}    — 正文增量（逐 token 渲染）
        {type: 'status', status}        — 状态机变更
      """
  ```
  Pattern: `growth-agent/backend/app/qastep/state_machine.py:104`
- JS: Plain `//` comments explaining business logic, no JSDoc

**Architecture doc references in comments:**
- Comments frequently reference document sections: "技术架构文档 §7.3", "设计规格 §4.2", "协议 §3.2"
- These serve as traceability anchors to the design spec

## Function Design

**Size:** Functions generally <50 lines. Large pipeline methods (e.g., `QAStepPipeline.run()`) reach ~70 lines but are well-structured with clear sections.

**Parameters:**
- Python: Type-annotated parameters with `Optional[T]` or `T | None` for optionals
- Keyword-only arguments for clarity: `async def persist_telemetry(self, qa_id: str, *, model, prompt_hash, ...)` — `growth-agent/backend/app/qastep/repository.py:156`
- Dataclasses for structured params: `@dataclass(frozen=True)` for config (`Settings`, `Thresholds`, `BreakerConfig`)

**Return Values:**
- Dict for API responses (not Pydantic response models on all routes — some return raw dicts)
- `AsyncIterator[dict]` for streaming generators
- `Optional[T]` for operations that may not find a result
- Dataclass instances for internal data (`CreatedQA`, `Checkpoint`)
- Pydantic models for request validation (`QAStartRequest`, `MergeRequest`)

## Module Design

**Exports:**
- `__init__.py` files use explicit `__all__` lists
  - `growth-agent/backend/app/qastep/__init__.py` — exports `QAStatus`, `QAStepPipeline`, `repo`, etc.
  - `growth-agent/backend/app/harness/__init__.py` — exports full public API surface
  - `growth-agent/backend/app/api/__init__.py` — exports router instances
- Pattern: module exports a singleton instance alongside the class
  - `repo = QAStepRepository()` — `growth-agent/backend/app/qastep/repository.py:191`
  - `concept_service = ConceptService()` — `growth-agent/backend/app/concept/service.py:185`
  - `normalizer = ConceptNormalizer()` — `growth-agent/backend/app/concept/normalization.py:207`
  - `threshold_service = ThresholdService(...)` — `growth-agent/backend/app/concept/thresholds.py:68`
  - `settings = Settings()` — `growth-agent/backend/app/config.py:40`

**Barrel Files:**
- `__init__.py` acts as barrel for each package, re-exporting public API
- `from app.concept import concept_service, normalizer, threshold_service` — consumers import from package, not modules directly

**Protocol pattern (structural typing):**
- `Protocol` classes define interfaces without implementation
  - `InferenceSession` Protocol — `growth-agent/backend/app/inference/protocol.py:32`
  - `Embedder`, `LLMJudge` Protocols — `growth-agent/backend/app/concept/normalization.py:25,31`
  - `LLMBackend` Protocol — `growth-agent/backend/app/inference/backend.py:23`
- Stub implementations for testing/local dev:
  - `StubInferenceSession`, `FailingInferenceSession` — `growth-agent/backend/app/inference/protocol.py:123,147`
  - `StubLLMBackend` — `growth-agent/backend/app/inference/backend.py:45`
  - `MockEmbedder`, `MockLLMJudge` — `growth-agent/backend/app/concept/normalization.py:38,60`

**Immutability:**
- `@dataclass(frozen=True)` for config objects: `Settings`, `Thresholds`
- Frontend store uses immutable updates: `set({ stack: [...state.stack, layer] })` — `growth-agent/frontend/src/store/qaStore.js:36`

---

*Convention analysis: 2026-08-11*
