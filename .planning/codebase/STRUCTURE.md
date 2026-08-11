# Codebase Structure

**Analysis Date:** 2026-08-11

## Directory Layout

```
starMindAgent/
├── .planning/codebase/     # GSD analysis documents (this folder)
├── golden_concept_clusters.json  # Golden concept clusters data (top-level)
├── growth-agent/           # PRIMARY variant — full stack (backend + frontend + eval)
│   ├── backend/
│   │   ├── app/            # FastAPI application (main package)
│   │   ├── migrations/    # SQL migration scripts
│   │   ├── tests/          # pytest test suite
│   │   ├── requirements.txt
│   │   └── pytest.ini
│   ├── frontend/           # React + Vite + Cytoscape.js frontend
│   ├── eval/               # Evaluation pipeline (golden set + evaluators)
│   ├── scripts/            # Telemetry export bridge
│   ├── docker-compose.yml  # PG + backend + frontend
│   ├── start.sh            # One-click dev start
│   ├── README.md           # Architecture-to-file mapping doc
│   └── INTEGRATION_NOTES.md  # Real inference integration notes
├── growth-agent-harness/   # Variant: includes harness/ + harness/sentinel.py
├── growth-agent-integrated/# Variant: harness integrated, no inference/{client,backend,constraints,context,sentinel}.py
├── growth-agent-ref-impl/  # Variant: reference impl, no harness/, no inference extras
├── growth_inference_client/# Standalone: inference client module only (backend/app/inference/* + tests)
└── growup_assess_v1/       # Standalone: eval pipeline (src/ mirrors growth-agent/eval/src/)
```

## Directory Purposes

**`growth-agent/` (primary):**
- Purpose: Full-stack reference implementation of "伴你成长" learning agent
- Contains: backend (FastAPI), frontend (React/Vite), eval pipeline, scripts, docker-compose
- Key files: `backend/app/main.py`, `backend/app/qastep/state_machine.py`, `backend/app/concept/service.py`, `frontend/src/App.jsx`

**`growth-agent/backend/app/`:**
- Purpose: FastAPI application package — all backend logic
- Contains: API routes, domain logic (qastep, concept), harness lifecycle, inference layer, ORM models, schemas, config, DB session
- Key files: `main.py` (entry), `api/routes_qa.py`, `qastep/state_machine.py`, `concept/service.py`, `harness/app.py`

**`growth-agent/backend/app/api/`:**
- Purpose: HTTP/SSE endpoint definitions
- Contains: FastAPI `APIRouter` modules — `routes_qa.py` (QAStep 3 exits + SSE), `routes_concept.py` (merge/undo/graph/explore), `routes_harness.py` (observability)
- Key files: `routes_qa.py`, `routes_concept.py`, `routes_harness.py`

**`growth-agent/backend/app/qastep/`:**
- Purpose: QAStep state machine + persistence
- Contains: `state_machine.py` (QAStepPipeline, QAStatus, transitions), `repository.py` (optimistic-lock CRUD, fork, telemetry), `telemetry.py` (NDJSON emitter)
- Key files: `state_machine.py`, `repository.py`, `telemetry.py`

**`growth-agent/backend/app/concept/`:**
- Purpose: Concept graph service + normalization
- Contains: `service.py` (merge/undo/graph/explore), `normalization.py` (3-level pipeline), `audit.py` (undo replay), `thresholds.py` (hot-reload config)
- Key files: `service.py`, `normalization.py`, `audit.py`, `thresholds.py`

**`growth-agent/backend/app/harness/`:**
- Purpose: Production-grade inference session lifecycle
- Contains: `manager.py` (InferenceSessionManager), `inference_session.py` (production Protocol impl), `circuit_breaker.py` (breaker + retry + caller), `reannotation.py` (worker pool + task stores), `recovery.py` (recovery coordinator), `store.py` (checkpoint stores), `observability.py` (metrics + router), `sentinel.py` (SentinelDetector + JsonAccumulator), `inference_client.py` (InferenceClient Protocol + StubInferenceClient), `models.py` (dataclass models + enums), `app.py` (assembly + demo)
- Key files: `manager.py`, `inference_session.py`, `circuit_breaker.py`, `reannotation.py`, `app.py`

**`growth-agent/backend/app/inference/`:**
- Purpose: LLM inference layer — Protocol + client + backend + sentinel detection
- Contains: `protocol.py` (InferenceSession Protocol + StreamSplitter + StubInferenceSession), `client.py` (InferenceClient with L0-L3), `backend.py` (LLMBackend Protocol + StubLLMBackend + OpenAICompatibleBackend), `sentinel.py` (SentinelDetector + split_stream), `constraints.py` (guided decoding probe + ConceptBlock parsing), `context.py` (prompt builder with token budget), `tasks.py` (BackfillQueue + backfill_processor)
- Key files: `protocol.py`, `client.py`, `backend.py`, `sentinel.py`

**`growth-agent/backend/app/models/`:**
- Purpose: ORM models
- Contains: `tables.py` (Base + ConceptNode, ConceptEdge, QASession, QAStep, AuditLog, BackfillTask)
- Key files: `tables.py`

**`growth-agent/backend/app/schemas/`:**
- Purpose: Pydantic request/response models
- Contains: `__init__.py` (QAStartRequest, QAStepOut, DriftDownRequest, RollbackRequest, MergeRequest, UndoMergeRequest, GraphRequest, ExploreRequest, ConceptBlock, ConceptItem)
- Key files: `__init__.py`

**`growth-agent/backend/migrations/`:**
- Purpose: SQL DDL scripts
- Contains: `001_init.sql` (core tables + views + triggers), `002_harness.sql` (harness_checkpoint + harness_task tables)
- Key files: `001_init.sql`, `002_harness.sql`

**`growth-agent/backend/tests/`:**
- Purpose: Pytest test suite
- Contains: `conftest.py`, `test_qastep.py`, `test_concept_service.py`, `test_normalization.py`, `test_integration.py`, `test_checkpoint_recovery.py`, `test_circuit_breaker.py`, `test_inference_session.py`, `test_observability.py`, `test_reannotation.py`, `test_sentinel.py`, plus `harness/` and `inference/` subdirs
- Key files: `conftest.py`, `test_integration.py`

**`growth-agent/frontend/src/`:**
- Purpose: React frontend
- Contains: `main.jsx` (entry), `App.jsx` (root), `components/TreeView.jsx`, `components/ConceptGraph.jsx`, `store/qaStore.js`, `api/client.js`
- Key files: `App.jsx`, `store/qaStore.js`, `api/client.js`, `components/ConceptGraph.jsx`

**`growth-agent/eval/`:**
- Purpose: Evaluation pipeline
- Contains: `src/` (evaluators: extraction, normalization, drilldown, hierarchy, nonfunctional, replay, matcher, llm_judge, report_generator, models, data_contracts), `scripts/run_eval.py` (main entry), `golden_set/` (JSON golden sets), `templates/`, `config/`
- Key files: `src/replay.py`, `src/extraction_eval.py`, `scripts/run_eval.py`

**`growth-agent/scripts/`:**
- Purpose: Dev utility scripts
- Contains: `export_telemetry.py` (NDJSON → replay JSON bridge)
- Key files: `export_telemetry.py`

**`growth_inference_client/`:**
- Purpose: Standalone inference client module (subset of growth-agent/backend/app/inference/)
- Contains: `backend/app/inference/{backend,client,constraints,context,sentinel}.py`, `backend/tests/inference/test_{client,constraints,context,sentinel}.py`
- Key files: `backend/app/inference/client.py`

**`growup_assess_v1/`:**
- Purpose: Standalone eval pipeline (mirrors growth-agent/eval/src/)
- Contains: `src/` (same evaluators as eval/src/), `scripts/run_eval.py`, `golden_set/`, `templates/`, `config/`, `generate_golden_set.py`, `demo_report.md`
- Key files: `src/replay.py`, `generate_golden_set.py`

## Key File Locations

**Entry Points:**
- `growth-agent/backend/app/main.py`: FastAPI app creation, lifespan, router mounting
- `growth-agent/frontend/src/main.jsx`: React DOM mount
- `growth-agent/eval/scripts/run_eval.py`: Eval pipeline CLI entry
- `growth-agent/start.sh`: One-click backend+frontend dev start
- `growth-agent/backend/app/harness/app.py`: Harness lifecycle demo (`python -m app.harness.app`)

**Configuration:**
- `growth-agent/backend/app/config.py`: `Settings` dataclass with env defaults (DATABASE_URL, concept_sentinel, timeouts, bloat limits)
- `growth-agent/backend/pytest.ini`: pytest-asyncio auto mode
- `growth-agent/backend/requirements.txt`: Python dependencies (fastapi, uvicorn, sqlalchemy, asyncpg, pydantic, pytest)
- `growth-agent/frontend/package.json`: JS dependencies (react 18, cytoscape 3, vite 5)
- `growth-agent/frontend/vite.config.js`: Vite config with `/qa`, `/concept` proxy to :8000
- `growth-agent/docker-compose.yml`: PG 16 + backend + frontend services

**Core Logic:**
- `growth-agent/backend/app/qastep/state_machine.py`: QAStepPipeline, QAStatus, QATransition
- `growth-agent/backend/app/qastep/repository.py`: QAStepRepository (CRUD, fork, telemetry, tree query)
- `growth-agent/backend/app/concept/service.py`: ConceptService (merge, undo, get_graph, increment_explore, color_tier)
- `growth-agent/backend/app/concept/normalization.py`: ConceptNormalizer (3-level pipeline, MockEmbedder, MockLLMJudge)
- `growth-agent/backend/app/harness/manager.py`: InferenceSessionManager (start, abort, resume, get, state)
- `growth-agent/backend/app/harness/inference_session.py`: production InferenceSession (stream, checkpoint, degrade)
- `growth-agent/backend/app/harness/circuit_breaker.py`: CircuitBreaker, CircuitBreakerRegistry, ResilientCaller, RetryPolicy
- `growth-agent/backend/app/harness/reannotation.py`: WorkerPool, InMemoryTaskStore, SqlTaskStore, default handlers
- `growth-agent/backend/app/inference/protocol.py`: InferenceSession Protocol, StreamSplitter, StubInferenceSession
- `growth-agent/backend/app/inference/client.py`: InferenceClient (L0-L3 degradation)
- `growth-agent/backend/app/inference/backend.py`: LLMBackend Protocol, StubLLMBackend, OpenAICompatibleBackend

**Data Models:**
- `growth-agent/backend/app/models/tables.py`: ConceptNode, ConceptEdge, QASession, QAStep, AuditLog, BackfillTask
- `growth-agent/backend/app/schemas/__init__.py`: ConceptBlock, ConceptItem, QAStartRequest, QAStepOut, DriftDownRequest, RollbackRequest, MergeRequest, UndoMergeRequest, GraphRequest, ExploreRequest
- `growth-agent/backend/app/harness/models.py`: Checkpoint, StreamChunk, InferenceRequest, HarnessTimeouts, SessionStatus, JsonState, DegradeLevel, SENTINEL

**Migrations:**
- `growth-agent/backend/migrations/001_init.sql`: concept_node, concept_edge, qa_session, qa_step, audit_log, backfill_task + views + triggers
- `growth-agent/backend/migrations/002_harness.sql`: harness_checkpoint, harness_task

**Testing:**
- `growth-agent/backend/tests/conftest.py`: Shared fixtures
- `growth-agent/backend/tests/test_integration.py`: E2E integration tests (L0/L1 with harness)
- `growth-agent/backend/tests/test_qastep.py`: State machine + repository tests
- `growth-agent/backend/tests/test_concept_service.py`: Merge/undo/graph tests
- `growth-agent/backend/tests/harness/`: Harness-specific tests
- `growth-agent/backend/tests/inference/`: Inference client/sentinel tests
- `growth-agent/eval/tests/`: Eval pipeline tests
- `growth_inference_client/backend/tests/inference/`: Standalone inference tests

**Telemetry & Eval:**
- `growth-agent/backend/app/qastep/telemetry.py`: NDJSON emitter (11 fields)
- `growth-agent/scripts/export_telemetry.py`: NDJSON → replay JSON
- `growth-agent/eval/src/replay.py`: ReplayComparator (qa_id replay vs golden set)
- `growth-agent/eval/golden_set/`: Golden set JSON files (computer_networks, database_systems, machine_learning, schema)

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (e.g., `state_machine.py`, `circuit_breaker.py`, `routes_qa.py`)
- Route modules: `routes_<domain>.py` (e.g., `routes_qa.py`, `routes_concept.py`, `routes_harness.py`)
- Test files: `test_<module>.py` (e.g., `test_qastep.py`, `test_integration.py`)
- Frontend components: `PascalCase.jsx` (e.g., `TreeView.jsx`, `ConceptGraph.jsx`)
- Frontend utilities: `camelCase.js` (e.g., `qaStore.js`, `client.js`)
- SQL migrations: `NNN_description.sql` (e.g., `001_init.sql`, `002_harness.sql`)

**Directories:**
- Python packages: `snake_case` (e.g., `qastep`, `concept`, `harness`, `inference`)
- Frontend: `src/components/`, `src/store/`, `src/api/` (functional grouping)
- Tests: `tests/` with `harness/`, `inference/` subdirs mirroring `app/` structure

## Where to Add New Code

**New API Endpoint:**
- Route file: Add to existing `growth-agent/backend/app/api/routes_<domain>.py` or create new `routes_<domain>.py` + register in `growth-agent/backend/app/api/__init__.py` + include in `main.py`
- Schema: Add request/response Pydantic models to `growth-agent/backend/app/schemas/__init__.py`
- Service logic: Add to corresponding domain module (`qastep/`, `concept/`, or new module under `app/`)
- Test: Add `test_<name>.py` in `growth-agent/backend/tests/`

**New QAStep Exit / Transition:**
- State machine: Add `QATransition` enum value in `growth-agent/backend/app/qastep/state_machine.py:32`
- Pipeline method: Add to `QAStepPipeline` class (`state_machine.py:82`)
- Repository: Add persistence method in `growth-agent/backend/app/qastep/repository.py`
- Route: Add endpoint in `growth-agent/backend/app/api/routes_qa.py`
- Frontend: Add action in `growth-agent/frontend/src/components/TreeView.jsx` + API call in `src/api/client.js` + store update in `src/store/qaStore.js`

**New Concept Service Operation:**
- Service: Add method to `ConceptService` in `growth-agent/backend/app/concept/service.py`
- Route: Add endpoint in `growth-agent/backend/app/api/routes_concept.py`
- Schema: Add request model in `growth-agent/backend/app/schemas/__init__.py`
- Audit: If merge/undo-like, add `audit_log` entry with snapshot in `growth-agent/backend/app/concept/audit.py`

**New Inference Backend:**
- Backend impl: Add class implementing `LLMBackend` Protocol in `growth-agent/backend/app/inference/backend.py` (or new file under `inference/`)
- Selection: Update `default_backend()` in `backend.py` to detect via env vars
- Test: Add `test_<backend>.py` in `growth-agent/backend/tests/inference/`

**New Harness Component:**
- Implementation: Add module under `growth-agent/backend/app/harness/`
- Assembly: Wire into `build_harness()` in `growth-agent/backend/app/harness/app.py`
- Export: Add to `__init__.py` `__all__` list
- Observability: If metrics-bearing, wire into `MetricsCollector` in `observability.py`

**New ORM Model:**
- Model: Add class to `growth-agent/backend/app/models/tables.py` (inherit `Base`)
- Migration: Add `CREATE TABLE` DDL to new `migrations/NNN_<name>.sql`
- Docker: Mount new migration in `docker-compose.yml` if needed

**New Eval Mode:**
- Evaluator: Add module in `growth-agent/eval/src/` (e.g., `<name>_eval.py`)
- CLI: Add `--mode` branch in `growth-agent/eval/scripts/run_eval.py`
- Models: Add data models in `growth-agent/eval/src/models.py`
- Report: Wire into `report_generator.py`

**New Frontend Component:**
- Component: Add `.jsx` file in `growth-agent/frontend/src/components/`
- Store: If stateful, add to `growth-agent/frontend/src/store/qaStore.js`
- API: Add fetch wrapper in `growth-agent/frontend/src/api/client.js`
- Mount: Import in `App.jsx`

**Utilities:**
- Shared backend helpers: Place in domain module (e.g., `growth-agent/backend/app/concept/` for concept-related utils)
- Cross-cutting: Place in `growth-agent/backend/app/` root (alongside `config.py`, `db.py`)
- Scripts: Place in `growth-agent/scripts/`

## Special Directories

**`growth-agent/backend/migrations/`:**
- Purpose: SQL DDL scripts, run by `psql` or docker-entrypoint-initdb.d
- Generated: No (hand-written)
- Committed: Yes

**`growth-agent/eval/golden_set/`:**
- Purpose: Golden set JSON files for eval replay
- Generated: Partially — `schema.json` is hand-written; domain files (e.g., `machine_learning.json`) can be generated by `growup_assess_v1/generate_golden_set.py`
- Committed: Yes

**`telemetry/qa_steps/` (runtime):**
- Purpose: NDJSON telemetry output, created at runtime by `qastep/telemetry.py`
- Generated: Yes (runtime, `TELEMETRY_DIR` env, default `telemetry/qa_steps/<date>/<qa_id>.jsonl`)
- Committed: No (runtime artifact)

**`.pytest_cache/`:**
- Purpose: pytest cache
- Generated: Yes
- Committed: No

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes
- Committed: No

---

*Structure analysis: 2026-08-11*
