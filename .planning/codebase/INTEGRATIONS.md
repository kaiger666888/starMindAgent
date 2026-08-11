# External Integrations

**Analysis Date:** 2026-08-11

## APIs & External Services

**LLM Inference (OpenAI-Compatible Chat Completions):**
- Service: OpenAI-compatible streaming LLM backend
  - SDK/Client: `httpx.AsyncClient` / `httpx.AsyncStream` (lazy import in `growth-agent/backend/app/inference/backend.py:117,147`)
  - Auth: `LLM_API_KEY` env var (Bearer token)
  - Endpoint: `{LLM_BASE_URL}/chat/completions`
  - Model: `LLM_MODEL` (default `gpt-4o-mini`), failover: `LLM_BACKUP_MODEL`
  - Protocol: Streaming SSE (`"stream": true`) for prose + sentinel + ConceptBlock JSON; non-streaming for `extract_only` calls
  - Activation: Only active when `LLM_BASE_URL` is set; otherwise falls back to `StubLLMBackend` (`growth-agent/backend/app/inference/backend.py:161-168`)
  - Contract: `LLMBackend` Protocol at `growth-agent/backend/app/inference/backend.py:22-39`

**LLM-as-Judge (Evaluation Pipeline):**
- Service: OpenAI Chat Completions API (non-streaming)
  - SDK/Client: `openai.OpenAI` (lazy import in `growup_assess_v1/src/llm_judge.py:88`)
  - Auth: `OPENAI_API_KEY` env var
  - Model: `gpt-4o` (default, configurable via `JudgeConfig.model`)
  - Purpose: 5-dimension rubric scoring of drilldown answers (concept_relevance, explanation_accuracy, depth_adaptability, context_coherence)
  - Optional: `base_url` override for OpenAI-compatible endpoints

**Harness InferenceClient Protocol (Pluggable):**
- Service: Inference framework layer (contract-based, implementation injected at runtime)
  - SDK/Client: `InferenceClient` Protocol at `growth-agent/backend/app/harness/inference_client.py:26-36`
  - Implementation: `StubInferenceClient` (default) at `growth-agent/backend/app/harness/inference_client.py:63-133`
  - Real implementation: Provided by "inference framework engineer" via dependency injection in `build_harness()` (`growth-agent/backend/app/harness/app.py:50`)
  - Methods: `stream(req)` -> `AsyncIterator[StreamChunk]`, `abort(call_id)`, `extract_only(text, model) -> ConceptBlock`

## Data Storage

**Databases:**
- PostgreSQL 16 (primary production database)
  - Connection: `DATABASE_URL` env var (default: `postgresql+asyncpg://dev:dev@localhost:5432/growth_agent`)
  - Client: SQLAlchemy 2.0 async engine with `asyncpg` driver (`growth-agent/backend/app/db.py:23`)
  - Extension: `pgcrypto` (for `gen_random_uuid()`)
  - Tables: `concept_node`, `concept_edge`, `qa_session`, `qa_step`, `audit_log`, `backfill_task`, `harness_checkpoint`, `harness_task`
  - Migrations: `growth-agent/backend/migrations/001_init.sql`, `growth-agent/backend/migrations/002_harness.sql`
  - ORM Models: `growth-agent/backend/app/models/tables.py`

- SQLite (local dev / test fallback)
  - Connection: `sqlite+aiosqlite:///./growth_agent.db` (commented in `.env.example`)
  - Client: SQLAlchemy 2.0 async with `aiosqlite` driver
  - Note: JSONB columns and GIN indexes are PostgreSQL-specific; SQLite mode is for logic tests that don't touch DB

**File Storage:**
- Local filesystem - QAStep telemetry NDJSON files
  - Location: `TELEMETRY_DIR` env var (default: `telemetry/qa_steps/`)
  - Writer: `growth-agent/backend/app/qastep/telemetry.py:emit_telemetry()` appends NDJSON per `qa_id`
  - Reader: `growth-agent/scripts/export_telemetry.py` aggregates NDJSON into replay JSON
  - Pattern: `{TELEMETRY_DIR}/{date}/{qa_id}.jsonl`

- Local filesystem - Thresholds JSON
  - Location: `THRESHOLDS_PATH` env var (default: `growth-agent/backend/app/thresholds.local.json`)
  - Hot-reload: `ThresholdService` checks file mtime and reloads on change (`growth-agent/backend/app/concept/thresholds.py:46-58`)

- Local filesystem - Golden set JSON (evaluation)
  - Location: `growth-agent/eval/golden_set/*.json` and `growup_assess_v1/golden_set/*.json`
  - Schema: `growth-agent/eval/golden_set/schema.json`

**Caching:**
- In-memory only - `asyncio.Queue` for process-local backfill task queue (`growth-agent/backend/app/inference/tasks.py:27`)
- In-memory only - `InMemoryCheckpointStore` and `InMemoryTaskStore` for harness state (`growth-agent/backend/app/harness/app.py:77-78`)
- Note: Production can replace with Celery / RQ / DB polling worker (noted in `growth-agent/backend/app/inference/tasks.py:9-10`)

## Authentication & Identity

**Auth Provider:**
- None (Custom / Not implemented)
  - Implementation: No authentication layer in backend routes; CORS allows all origins (`allow_origins=["*"]` in `growth-agent/backend/app/main.py:38`)
  - `qa_session` table has `user_id` column (`growth-agent/backend/app/models/tables.py:66`) but no auth middleware populates it
  - LLM API key auth (`LLM_API_KEY`) is backend-to-LLM only, not user-facing

## Monitoring & Observability

**Error Tracking:**
- None (Python `logging` module only)
  - Logging: `logging.basicConfig(level=logging.INFO)` in `growth-agent/backend/app/main.py:17`
  - Named loggers: `growth-agent`, `app.inference.client`, `app.harness.manager`, etc.

**Logs:**
- Python standard `logging` module (INFO level, format: `%(asctime)s %(levelname)s %(name)s: %(message)s`)

**Metrics:**
- Custom FastAPI endpoint: `GET /harness/obs/metrics`
  - Implementation: `growth-agent/backend/app/harness/observability.py:MetricsCollector`
  - Routes: `growth-agent/backend/app/api/routes_harness.py` (only in `growth-agent-integrated`)
  - Four core metrics: interruption recovery success rate, circuit breaker error rate per endpoint, async reannotation completion rate + dead letter rate, backfill P95 latency
  - Gate values inlined in response (e.g., `"> 95%"`, `"> 10%/h"`, `"completion > 90%, dead_letter < 2%"`, `"< 30000 ms"`)

**Circuit Breaker:**
- Custom implementation at `growth-agent/backend/app/harness/circuit_breaker.py`
  - Per-endpoint breaker registry (`CircuitBreakerRegistry`)
  - States: CLOSED / OPEN / HALF_OPEN
  - Failover map support (`ResilientCaller`)

## CI/CD & Deployment

**Hosting:**
- Docker containers (via `docker-compose.yml`)
  - PostgreSQL 16 container (image: `postgres:16`)
  - Backend container (built from `growth-agent/backend/`)
  - Frontend container (built from `growth-agent/frontend/`)

**CI Pipeline:**
- None detected (no `.github/workflows/`, no CI config files found)

**Docker Compose:**
- File: `growth-agent/docker-compose.yml`
- Services: `db` (PostgreSQL 16, port 5432), `backend` (uvicorn, port 8000), `frontend` (Vite, port 5173)
- Volumes: `pgdata` (PostgreSQL data), `telemetry` (NDJSON telemetry files)
- Init: SQL migrations mounted as `docker-entrypoint-initdb.d/` scripts

## Environment Configuration

**Required env vars:**
- `DATABASE_URL` - PostgreSQL async connection string
- `CONCEPT_SENTINEL` - Sentinel string for LLM output protocol (default: `≡≡CONCEPT_BLOCK≡≡`)
- `INFERENCE_TIMEOUT_S` - Overall LLM call timeout (default: 60)
- `FIRST_TOKEN_TIMEOUT_S` - First token timeout (default: 5)
- `JSON_PARSE_TIMEOUT_S` - JSON parse timeout (default: 15)
- `MAX_EXPLORE_DEPTH` - Drill-down depth limit (default: 6)
- `MAX_CONCEPTS_PER_SESSION` - Concept count limit (default: 200)
- `BACKFILL_MAX_RETRY` - Backfill retry count (default: 3)
- `THRESHOLDS_PATH` - Thresholds JSON path (default: `app/thresholds.local.json`)
- `TELEMETRY_DIR` - Telemetry output directory (default: `telemetry/qa_steps`)

**Optional env vars (LLM integration):**
- `LLM_BASE_URL` - OpenAI-compatible API base URL (absent = stub backend)
- `LLM_API_KEY` - LLM API key (Bearer token)
- `LLM_MODEL` - Primary model name (default: `gpt-4o-mini`)
- `LLM_BACKUP_MODEL` - Failover model name

**Optional env vars (evaluation):**
- `OPENAI_API_KEY` - For LLM-as-judge evaluation pipeline

**Secrets location:**
- Environment variables / `.env` file (not committed; `.env.example` is the template)
- No external secrets manager detected

## Webhooks & Callbacks

**Incoming:**
- None (no webhook receiver endpoints detected)

**Outgoing:**
- SSE (Server-Sent Events) streaming to frontend clients
  - Endpoint: `GET /qa/{qa_id}/stream` (`growth-agent/backend/app/api/routes_qa.py:53`)
  - Media type: `text/event-stream`
  - Event types: `answer_delta`, `status`, `concepts`, `done`, `error`, `degraded`
  - Frontend consumer: `growth-agent/frontend/src/api/client.js:subscribeStream()` uses `EventSource` API
  - Reconnection: Harness supports `last-event-id` based resume (`growth-agent/backend/app/harness/manager.py:83`)

---

*Integration audit: 2026-08-11*
