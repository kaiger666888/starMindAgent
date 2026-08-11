<!-- refreshed: 2026-08-11 -->
# Architecture

**Analysis Date:** 2026-08-11

## System Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│                         Frontend (React/Vite)                     │
│  `growth-agent/frontend/src/`                                     │
├──────────────────┬────────────────────────────────────────────────┤
│   TreeView.jsx   │   ConceptGraph.jsx (Cytoscape.js)             │
│   (探索栈 + 3 出口)│   (三状态着色 + 力导向图)                       │
└────────┬─────────┴───────────┬──────────────────────────────────-┘
         │ fetch / EventSource SSE│
         ▼                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                    API Layer (FastAPI)                            │
│  `growth-agent/backend/app/api/`                                 │
│  routes_qa.py (3 出口) │ routes_concept.py │ routes_harness.py  │
└────────┬─────────────────────────┬──────────────────┬──────────-┘
         │                          │                  │
         ▼                          ▼                  ▼
┌─────────────────────┐  ┌────────────────────┐  ┌──────────────────┐
│  QAStep State Machine│  │  Concept Service  │  │  Harness Layer   │
│  `app/qastep/`       │  │  `app/concept/`   │  │  `app/harness/`  │
│  state_machine.py    │  │  service.py       │  │  manager.py      │
│  repository.py       │  │  normalization.py │  │  inference_      │
│  telemetry.py       │  │  audit.py         │  │    session.py    │
│  (generating→        │  │  thresholds.py    │  │  circuit_        │
│   extracting→        │  │                   │  │    breaker.py    │
│   waiting)           │  │  (merge/undo/     │  │  reannotation.py │
│                      │  │   graph/explore)  │  │  recovery.py     │
│                      │  │                   │  │  observability.py│
└──────────┬───────────┘  └─────────┬────────┘  │  store.py        │
           │                        │           │  sentinel.py     │
           │                        │           └────────┬─────────┘
           │                        │                    │
           ▼                        ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│              Inference Layer (Protocol-based)                    │
│  `app/inference/`                                                │
│  protocol.py (InferenceSession Protocol + StreamSplitter)         │
│  client.py (InferenceClient: L0-L3 降级链)                        │
│  backend.py (LLMBackend: Stub / OpenAICompatible)                 │
│  sentinel.py (SentinelDetector) │ constraints.py │ context.py    │
│  tasks.py (BackfillQueue)                                         │
└──────────────────────────────┬──────────────────────────────────-┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Data Layer (PostgreSQL)                       │
│  `app/models/tables.py` (ORM) + `app/db.py` (async session)       │
│  `migrations/001_init.sql` + `migrations/002_harness.sql`        │
│  Tables: concept_node, concept_edge, qa_session, qa_step,        │
│          audit_log, backfill_task, harness_checkpoint,           │
│          harness_task                                            │
└──────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| FastAPI App | Application entry, lifespan (backfill worker startup), router mounting, CORS | `growth-agent/backend/app/main.py` |
| QA Routes | QAStep 3 出口 (start/stream/drilldown/rollback) + SSE streaming | `growth-agent/backend/app/api/routes_qa.py` |
| Concept Routes | merge/undo/graph/explore REST endpoints | `growth-agent/backend/app/api/routes_concept.py` |
| Harness Routes | `/harness/obs/metrics` + sub-endpoints for observability | `growth-agent/backend/app/api/routes_harness.py` |
| QAStep State Machine | `generating→extracting→waiting` transition logic + 3 出口 fork/rollback/new-tree | `growth-agent/backend/app/qastep/state_machine.py` |
| QAStep Repository | Optimistic-lock persistence, answer checkpoint, fork child, co-occurrence edges, telemetry fields | `growth-agent/backend/app/qastep/repository.py` |
| QAStep Telemetry | 11-field NDJSON emission for eval replay | `growth-agent/backend/app/qastep/telemetry.py` |
| Concept Service | merge/undo/get_graph/increment_explore + color tier | `growth-agent/backend/app/concept/service.py` |
| Concept Normalizer | 3-level normalization pipeline: alias exact → embedding recall → LLM gray-zone | `growth-agent/backend/app/concept/normalization.py` |
| Concept Audit | Undo replay from audit_log snapshot | `growth-agent/backend/app/concept/audit.py` |
| Threshold Service | Hot-reloadable normalization thresholds from JSON | `growth-agent/backend/app/concept/thresholds.py` |
| Inference Protocol | `InferenceSession` Protocol + `StreamSplitter` + `StubInferenceSession` | `growth-agent/backend/app/inference/protocol.py` |
| Inference Client | Real `InferenceClient` with L0-L3 degradation chain | `growth-agent/backend/app/inference/client.py` |
| LLM Backend | `LLMBackend` Protocol + `StubLLMBackend` + `OpenAICompatibleBackend` | `growth-agent/backend/app/inference/backend.py` |
| Sentinel Detector | Sliding-window sentinel detection + JSON accumulator | `growth-agent/backend/app/inference/sentinel.py` |
| Constraints | Guided decoding probe + ConceptBlock parsing + heuristic extraction | `growth-agent/backend/app/constraints.py` |
| Context Builder | Prompt assembly with ≤2K token budget + chain compression | `growth-agent/backend/app/inference/context.py` |
| Backfill Queue | In-process async queue + DB-persisted backfill tasks | `growth-agent/backend/app/inference/tasks.py` |
| Harness Manager | `InferenceSessionManager`: start/abort/resume/get lifecycle API | `growth-agent/backend/app/harness/manager.py` |
| Harness InferenceSession | Production `InferenceSession` implementing main Protocol, checkpoint, timeout, degrade | `growth-agent/backend/app/harness/inference_session.py` |
| Circuit Breaker | Per-endpoint breaker + `ResilientCaller` + `RetryPolicy` (body-not-retractable constraint) | `growth-agent/backend/app/harness/circuit_breaker.py` |
| Reannotation Worker | Worker pool with `InMemoryTaskStore` / `SqlTaskStore`, retry 3→dead, orphan reclaim | `growth-agent/backend/app/harness/reannotation.py` |
| Recovery Coordinator | User-rollback abort + reconnect resume, success-rate metrics | `growth-agent/backend/app/harness/recovery.py` |
| Checkpoint Store | `InMemoryCheckpointStore` / `SqlCheckpointStore` for qa_id+offset recovery | `growth-agent/backend/app/harness/store.py` |
| Observability | `MetricsCollector` + `/harness/obs/metrics` router (4 non-functional gates) | `growth-agent/backend/app/harness/observability.py` |
| Harness App | `build_harness()` assembly + `HarnessBundle` + lifecycle demo | `growth-agent/backend/app/harness/app.py` |
| ORM Models | SQLAlchemy 2.0 declarative models aligned with migrations | `growth-agent/backend/app/models/tables.py` |
| DB Session | Lazy-async engine + `session_scope` context manager | `growth-agent/backend/app/db.py` |
| Schemas | Pydantic request/response models + `ConceptBlock`/`ConceptItem` | `growth-agent/backend/app/schemas/__init__.py` |
| Config | `Settings` dataclass with env-driven defaults | `growth-agent/backend/app/config.py` |
| Frontend App | Root component: TreeView + ConceptGraph layout | `growth-agent/frontend/src/App.jsx` |
| Frontend Store | `useSyncExternalStore`-based QAStep stack + inflight mutex | `growth-agent/frontend/src/store/qaStore.js` |
| Frontend API Client | fetch + EventSource SSE client matching backend routes | `growth-agent/frontend/src/api/client.js` |
| Eval Pipeline | Extraction/normalization/drilldown/hierarchy/nonfunctional/replay evaluators | `growth-agent/eval/src/*.py` |
| Telemetry Bridge | NDJSON → replay JSON aggregation | `growth-agent/scripts/export_telemetry.py` |

## Pattern Overview

**Overall:** Layered Protocol-driven Architecture with Stub-Fallback

**Key Characteristics:**
- Protocol-based decoupling: `InferenceSession` and `LLMBackend` defined as Python `Protocol`, stubs (`StubInferenceSession`, `StubLLMBackend`) allow full offline run, real implementations injected by config
- Explicit state machine: QAStep uses `generating→extracting→waiting` with legal-transition table; illegal transitions raise `IllegalTransition`
- Optimistic locking: `qa_step.version` column, `UPDATE WHERE version=$expected`, rowcount=0 raises `OptimisticLockConflict`
- Audit-log-only normalization: All merge/keep/undo decisions append to `audit_log`; no destructive updates without snapshot
- Sentinel stream splitting: `≡≡CONCEPT_BLOCK≡≡` delimits prose from JSON; `SentinelDetector` uses sliding-window + prefix buffer (len(sentinel)-1) for cross-chunk detection
- Degradation chain L0→L3: L0 success; L1 JSON fail → backfill; L2 split two calls; L3 keyword fallback
- Stub-everywhere: `MockEmbedder`, `MockLLMJudge`, `StubInferenceSession`, `StubLLMBackend`, `StubInferenceClient` — all swappable via env vars or DI
- Telemetry-to-eval bridge: 11-field NDJSON per QAStep, exported to JSON for `--mode replay`

## Layers

**API Layer:**
- Purpose: HTTP/SSE endpoints, request validation, response serialization
- Location: `growth-agent/backend/app/api/`
- Contains: FastAPI `APIRouter` per domain (qa, concept, harness)
- Depends on: `qastep`, `concept`, `harness`, `schemas`
- Used by: Frontend via Vite proxy (`/qa`, `/concept` → `:8000`)

**Domain / State Machine Layer:**
- Purpose: QAStep lifecycle, concept graph operations, normalization
- Location: `growth-agent/backend/app/qastep/`, `growth-agent/backend/app/concept/`
- Contains: State machine, repository (persistence), normalizer, audit, thresholds
- Depends on: `models`, `db`, `schemas`, `config`
- Used by: API layer

**Harness Layer:**
- Purpose: Production-grade inference session lifecycle — checkpoint, circuit breaker, reannotation, recovery, observability
- Location: `growth-agent/backend/app/harness/`
- Contains: `InferenceSessionManager`, `InferenceSession`, `CircuitBreaker`, `WorkerPool`, `RecoveryCoordinator`, `MetricsCollector`, stores
- Depends on: `inference` (for `InferenceClient` Protocol), `concept`, `qastep`, `config`
- Used by: API layer (`routes_qa` via `get_harness().session_for()`), `routes_harness`

**Inference Layer:**
- Purpose: LLM call execution, sentinel detection, degradation chain, backfill queue
- Location: `growth-agent/backend/app/inference/`
- Contains: `InferenceSession` Protocol, `InferenceClient`, `LLMBackend` Protocol, `SentinelDetector`, `StreamSplitter`, `BackfillQueue`
- Depends on: `schemas`, `config`
- Used by: Harness layer, QAStep pipeline (via Protocol)

**Data Layer:**
- Purpose: ORM models, async DB session management
- Location: `growth-agent/backend/app/models/`, `growth-agent/backend/app/db.py`
- Contains: `ConceptNode`, `ConceptEdge`, `QASession`, `QAStep`, `AuditLog`, `BackfillTask` (ORM); `Base`, `session_scope`, `get_session`
- Depends on: SQLAlchemy 2.0 async, asyncpg
- Used by: All backend layers

**Frontend Layer:**
- Purpose: UI — tree view of QAStep stack + Cytoscape.js concept graph
- Location: `growth-agent/frontend/src/`
- Contains: `App.jsx`, `TreeView.jsx`, `ConceptGraph.jsx`, `qaStore.js`, `client.js`
- Depends on: React 18, Cytoscape.js 3.x, Vite 5
- Used by: End user via browser

**Eval Layer:**
- Purpose: Evaluation pipeline — extraction, normalization, drilldown, hierarchy, nonfunctional, replay
- Location: `growth-agent/eval/src/`, `growth-agent/eval/scripts/`
- Depends on: golden set JSON, telemetry NDJSON
- Used by: AI eval engineers via `run_eval.py`

## Data Flow

### Primary Request Path (QA → SSE → Concept Graph)

1. User submits question in `TreeView` → `api.startQA()` POST `/qa/start` (`growth-agent/frontend/src/components/TreeView.jsx:22`)
2. `routes_qa.start()` creates `QASession` + `QAStep` via `repo.create()` (`growth-agent/backend/app/api/routes_qa.py:33`)
3. Frontend opens SSE: `api.subscribeStream(qaId)` → GET `/qa/{qa_id}/stream` (`growth-agent/frontend/src/api/client.js:18`)
4. `routes_qa.stream()` builds `QAStepPipeline` with `StubInferenceSession` (or harness `InferenceSession`) and calls `pipe.run()` (`growth-agent/backend/app/api/routes_qa.py:53`)
5. `QAStepPipeline.run()` yields `{status:generating}` → iterates `inference.stream()` → emits `{answer_delta}` / `{sentinel}` / `{json_done}` events (`growth-agent/backend/app/qastep/state_machine.py:104`)
6. On `sentinel`: `repo.transition(qa_id, EXTRACTING)` + yield `{status:extracting}` (`growth-agent/backend/app/qastep/state_machine.py:131`)
7. On `json_done`: `normalizer.normalize(item)` per concept → `repo.link_co_occurrence()` → yield `{concepts}` (`growth-agent/backend/app/qastep/state_machine.py:142-159`)
8. `repo.persist_telemetry()` writes model/prompt_hash/raw_output/parsed_concepts/aliases/confidence to `qa_step` row (`growth-agent/backend/app/qastep/state_machine.py:162`)
9. `repo.transition(qa_id, WAITING)` → yield `{status:waiting}` + `{done}` (`growth-agent/backend/app/qastep/state_machine.py:172-174`)
10. Frontend `qaStore` updates stack layer, `ConceptGraph` calls `getGraph(sessionId)` → POST `/concept/graph` (`growth-agent/frontend/src/components/ConceptGraph.jsx:35`)

### Drill-Down Path (出口1: fork)

1. User clicks concept chip in `TreeView` → `onDrillDown(parentQaId, conceptId, conceptName)` (`growth-agent/frontend/src/components/TreeView.jsx:30`)
2. POST `/qa/{qa_id}/drilldown` with `{concept_id, question}` (`growth-agent/frontend/src/api/client.js:33`)
3. `routes_qa.drilldown()` → `pipe.drill_down()` → `repo.fork_child()` creates child QAStep with `parent_qa_id` + `depth=parent.depth+1` (`growth-agent/backend/app/qastep/repository.py:109`)
4. If `child_depth > max_explore_depth (6)`: raise `DepthLimitReached` → API returns 422 (`growth-agent/backend/app/api/routes_qa.py:79`)
5. `incrementExplore(conceptId)` POST `/concept/explore` → `concept_service.increment_explore()` → color_tier update (`growth-agent/backend/app/concept/service.py:148`)

### Rollback Path (出口2: stack restore)

1. User clicks "↑ 回到这层" in `TreeView` → `onRollback(targetQaId)` → `popToLayer(qaId)` (`growth-agent/frontend/src/components/TreeView.jsx:42`)
2. POST `/qa/{qa_id}/rollback` with `{target_qa_id}` (`growth-agent/frontend/src/api/client.js:44`)
3. `routes_qa.rollback()` → `repo.restore_context(target_qa_id)` returns answer + offset + concept_ids (`growth-agent/backend/app/qastep/repository.py:80`)

### L1 Degradation Path (JSON parse failure)

1. `InferenceSession.stream()` detects sentinel but JSON parse fails → `_structured_retry()` calls `client.extract_only()` once (15s timeout) (`growth-agent/backend/app/harness/inference_session.py:256`)
2. If retry fails: `_finish_degrade(endpoint, "L1", reason)` → checkpoint status=COMPLETED, degrade_level=L1 (`growth-agent/backend/app/harness/inference_session.py:301`)
3. `_fire_degrade("L1", reason)` → `manager._on_degrade(cp, "L1", reason)` → `reannotation_queue.enqueue()` (`growth-agent/backend/app/harness/manager.py:124`)
4. `WorkerPool._process()` → `backfill_handler(task)` → `client.extract_only(answer_snapshot)` → `normalizer.normalize()` → `repo.link_co_occurrence()` → update `qa_step.extracted_concept_ids` (`growth-agent/backend/app/harness/reannotation.py:347`)

**State Management:**
- Backend: All state in PostgreSQL; `qa_step.version` for optimistic lock; `harness_checkpoint` for streaming recovery; `audit_log` append-only for normalization decisions
- Frontend: `qaStore` global object with `useSyncExternalStore` subscription; `stack` array of layers; `inflight` field for mutex
- No in-memory global mutable state in backend except module-level singletons (`repo`, `normalizer`, `concept_service`, `threshold_service`) which are stateless or DB-backed

## Key Abstractions

**InferenceSession Protocol:**
- Purpose: Decouples QAStep state machine from inference layer details (sentinel, JSON, degradation)
- Examples: `growth-agent/backend/app/inference/protocol.py` (Protocol def), `growth-agent/backend/app/harness/inference_session.py` (production impl), `growth-agent/backend/app/inference/protocol.py::StubInferenceSession` (stub)
- Pattern: Python `Protocol` with `stream() -> AsyncIterator[{kind:delta|sentinel|json_done|error}]`

**LLMBackend Protocol:**
- Purpose: Decouples inference client from specific LLM SDK / gateway
- Examples: `growth-agent/backend/app/inference/backend.py` (`LLMBackend` Protocol, `StubLLMBackend`, `OpenAICompatibleBackend`)
- Pattern: `@runtime_checkable Protocol` with `stream()`, `extract_only()`, `abort()`; selected by `default_backend()` based on `LLM_BASE_URL` env

**QAStep State Machine:**
- Purpose: Explicit state machine per exploration layer, not recursion
- Examples: `growth-agent/backend/app/qastep/state_machine.py` (`QAStatus`, `QATransition`, `QAStepPipeline`)
- Pattern: Enum-based states + legal-transition table; `QAStepPipeline.run()` yields SSE events

**Three-State Graph (single-table + origin derivation):**
- Purpose: Avoid maintaining three separate graph copies; derive views from `concept_edge.origin`
- Examples: `growth-agent/backend/app/concept/service.py::get_graph()` + SQL views `v_graph_user_click`, `v_graph_co_occurrence`, `v_graph_domain`
- Pattern: Single `concept_edge` table with `origin` CHECK constraint; views filter by origin

**Audit-Log-Only Normalization:**
- Purpose: All merge/keep/undo decisions are append-only, replayable for eval
- Examples: `growth-agent/backend/app/concept/normalization.py::_audit()`, `growth-agent/backend/app/concept/service.py::merge_concepts()` (snapshot in payload), `growth-agent/backend/app/concept/audit.py::replay_undo()`
- Pattern: `audit_log` table with `action` (merge/keep/undo), `merge_id`, `payload` JSONB snapshot; undo = reverse-replay from snapshot

**Checkpoint Recovery (qa_id + offset):**
- Purpose: Resume streaming after network disconnect without restarting inference call
- Examples: `growth-agent/backend/app/harness/store.py` (`InMemoryCheckpointStore`, `SqlCheckpointStore`), `growth-agent/backend/app/harness/manager.py::resume()`
- Pattern: `Checkpoint` dataclass with `answer_checkpoint`, `offset`, `last_event_id`; `resume(qa_id, last_event_id)` replays events > last_event_id

## Entry Points

**Backend API:**
- Location: `growth-agent/backend/app/main.py`
- Triggers: `uvicorn app.main:app --reload --port 8000` or `./start.sh` or `docker-compose up`
- Responsibilities: FastAPI app creation, CORS, router mounting, lifespan (backfill worker startup)

**Frontend:**
- Location: `growth-agent/frontend/src/main.jsx` → `App.jsx`
- Triggers: `npm run dev` (Vite dev server on :5173, proxy to :8000)
- Responsibilities: Mount React app, render TreeView + ConceptGraph

**Eval Pipeline:**
- Location: `growth-agent/eval/scripts/run_eval.py`
- Triggers: `python eval/scripts/run_eval.py --mode {extraction|normalization|drilldown|hierarchy|nonfunctional|replay|full}`
- Responsibilities: Load golden set, run evaluators, generate report

**Harness Lifecycle Demo:**
- Location: `growth-agent/backend/app/harness/app.py` (`_lifecycle_demo`)
- Triggers: `python -m app.harness.app`
- Responsibilities: Demo L0 path without DB/FastAPI

**Telemetry Export:**
- Location: `growth-agent/scripts/export_telemetry.py`
- Triggers: `python scripts/export_telemetry.py --telemetry-dir telemetry/qa_steps --out telemetry.json`
- Responsibilities: Aggregate NDJSON to replay JSON for eval

## Architectural Constraints

- **Threading:** Single-process async event loop (asyncio). All I/O (DB, LLM streaming) via async/await. No worker threads except `WorkerPool` asyncio tasks (default 2 workers for reannotation).
- **Global state:** Module-level singletons: `repo` (`growth-agent/backend/app/qastep/repository.py:191`), `normalizer` (`normalization.py:207`), `concept_service` (`service.py:185`), `threshold_service` (`thresholds.py:68`), `backfill_queue` (`tasks.py:96`), `settings` (`config.py:40`). All stateless or DB-backed. `_merge_lock` (`service.py:25`) is an `asyncio.Lock` for merge/undo serialization. `_engine` / `_SessionLocal` in `db.py` are lazily-initialized module globals.
- **Circular imports:** `routes_qa.py` imports `app.concept.normalizer` and `app.inference` at module level; `app.harness.app` uses lazy imports (`from app.concept import normalizer` inside `build_harness()`) to avoid import-time coupling. `qastep/repository.py` imports `state_machine` inside method body (`from app.qastep.state_machine import QAStepRuntime`) to avoid cycle.
- **Body-not-retractable constraint:** Once prose starts streaming to frontend, any failure can only degrade (L1), never retry the whole call (`growth-agent/backend/app/harness/circuit_breaker.py:150` `RetryPolicy.allow_first_token_retry`).
- **Bloat control:** Single tree ≤6 depth (`max_explore_depth`), single session ≤200 concepts (`max_concepts_per_session`); enforced in `repo.fork_child()` + SQL trigger `enforce_bloat_limits()` (`migrations/001_init.sql:163`).
- **Context budget:** Prompt ≤2K tokens; `_CHAIN_BUDGET=1000`, `_QUESTION_BUDGET=400`, `_SYSTEM_BUDGET=400`, `_RESERVE=200` (`growth-agent/backend/app/inference/context.py:19-23`).
- **Merge serialization:** `concept_service.merge_concepts()` and `undo_merge()` hold `_merge_lock` (global `asyncio.Lock`) to prevent concurrent normalization writes.

## Anti-Patterns

### Direct DB access in route handlers

**What happens:** `routes_qa.py` imports `QASession` and `QAStep` ORM models directly inside endpoint functions and performs `session_scope()` queries, bypassing the repository layer (e.g., `start()` at `growth-agent/backend/app/api/routes_qa.py:35-41` creates `QASession` inline).
**Why it's wrong:** Bypasses the repository abstraction, making it harder to test routes in isolation and creating inconsistent persistence patterns (some writes in `repo`, some in routes).
**Do this instead:** Route handlers should only call repository/service methods. Move the `QASession` creation in `start()` into `QAStepRepository.create()` or a `SessionRepository`.

### `getattr` fallback for backend mode detection

**What happens:** `InferenceClient._is_split_mode()` uses `getattr(self.backend, "mode", "") == "split"` to detect if backend supports sentinel+JSON co-existence (`growth-agent/backend/app/inference/client.py:153`).
**Why it's wrong:** Relies on an optional attribute that only `StubLLMBackend` sets; real backends (`OpenAICompatibleBackend`) don't have `mode`, so split-mode detection silently fails.
**Do this instead:** Add `supports_structured_streaming: bool` to the `LLMBackend` Protocol and implement explicitly in each backend.

### Bare `except Exception` swallowing errors

**What happens:** Multiple locations catch `Exception` broadly and either log+continue or return None, e.g., `thresholds.py:56` (`_reload` catches all and falls back to defaults), `reannotation.py:265` (worker loop catches all and continues), `inference_session.py:321` (checkpoint save failure logged but ignored).
**Why it's wrong:** Masks unexpected errors (e.g., DB schema mismatch, coding bugs) as benign degradation; debugging requires log inspection.
**Do this instead:** Catch specific expected exceptions (`json.JSONDecodeError`, `asyncio.TimeoutError`, `sqlalchemy.exc.SQLAlchemyError`); let unexpected exceptions propagate to a top-level error handler.

## Error Handling

**Strategy:** Defensive degradation with audit trail — errors in non-critical paths (telemetry, checkpoint save, on_refresh callback) are logged but don't block the main QA flow; errors in critical paths (state transition, DB write) raise domain exceptions caught by routes.

**Patterns:**
- Domain exceptions: `IllegalTransition`, `OptimisticLockConflict`, `DepthLimitReached` in `qastep/state_machine.py`; `CircuitOpenError` in `circuit_breaker.py`; `ValueError` for merge-self in `service.py`
- HTTP mapping: `routes_qa.py` maps `DepthLimitReached` → 422, `IllegalTransition` → SSE error event; `routes_concept.py` maps `ValueError` → 400/404
- Degradation events: `InferenceSession` yields `{kind:'error', message, degrade:'L1'}` events consumed by `QAStepPipeline` which yields `{type:'error', message}` to frontend
- Stub fallback: `default_backend()` catches `OpenAICompatibleBackend` init failure → `StubLLMBackend`; `thresholds.py` catches JSON load failure → `_DEFAULT`
- Checkpoint save failure: logged via `log.exception` but doesn't break streaming (`inference_session.py:321`)

## Cross-Cutting Concerns

**Logging:** Python `logging` module; root config in `main.py` (`logging.basicConfig(level=INFO)`); per-module loggers (`log = logging.getLogger(__name__)`); no structured logging or correlation IDs.

**Validation:** Pydantic v2 schemas in `growth-agent/backend/app/schemas/__init__.py` for all request/response models; SQL CHECK constraints in migrations for enum-like fields (`status`, `origin`, `action`, `source`); `QAStepRuntime.assert_transition()` for state machine legality.

**Authentication:** None — `CORSMiddleware` allows `origins=["*"]`; no auth middleware; `qa_session.user_id` is optional and never populated by routes. Designed for local/single-tenant deployment.

---

*Architecture analysis: 2026-08-11*
