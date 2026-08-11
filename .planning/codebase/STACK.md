# Technology Stack

**Analysis Date:** 2026-08-11

## Languages

**Primary:**
- Python 3.10+ - Backend: FastAPI API server, inference layer, harness lifecycle, concept normalization, evaluation pipeline
- JavaScript (ES2020+) / JSX - Frontend: React SPA (Vite-based)

**Secondary:**
- SQL - Database migrations (`growth-agent/backend/migrations/001_init.sql`, `growth-agent/backend/migrations/002_harness.sql`)
- JSON - Configuration files (`thresholds.local.json`, `golden_concept_clusters.json`, golden set QA pairs)

## Runtime

**Environment:**
- Python 3.10+ (uses `from __future__ import annotations`, `dataclass`, `Protocol`, `AsyncIterator`)
- Node.js 18+ (Vite 5.x requires Node 18+)

**Package Manager:**
- pip (Python) - no lockfile; dependencies pinned with `>=` floor versions in `requirements.txt`
- npm (JavaScript) - no lockfile committed; dependencies declared in `package.json`

## Frameworks

**Core:**
- FastAPI >=0.110 - Backend REST API server; provides async route handlers, Pydantic validation, SSE StreamingResponse (`growth-agent/backend/app/main.py`)
- React 18.3.1 - Frontend SPA UI library (`growth-agent/frontend/package.json`)
- Vite 5.4.0 - Frontend dev server and build tool (`growth-agent/frontend/vite.config.js`)
- SQLAlchemy 2.0+ (async) - ORM and database session management (`growth-agent/backend/app/db.py`, `growth-agent/backend/app/models/tables.py`)

**Testing:**
- pytest >=8.0 - Python test runner (`growth-agent/backend/pytest.ini`)
- pytest-asyncio >=0.23 - Async test support; `asyncio_mode = auto`

**Build/Dev:**
- uvicorn[standard] >=0.27 - ASGI server for FastAPI (`growth-agent/backend/app/main.py`)
- @vitejs/plugin-react 4.3.1 - React plugin for Vite

## Key Dependencies

**Critical:**
- asyncpg >=0.29 - PostgreSQL async driver for SQLAlchemy async engine (`growth-agent/backend/app/db.py:23`)
- aiosqlite >=0.19 - SQLite async driver (local dev / test fallback for PostgreSQL)
- pydantic >=2.6 - Request/response schema validation; `ConceptBlock` / `ConceptItem` contracts (`growth-agent/backend/app/schemas/__init__.py`)
- httpx - HTTP client for OpenAI-compatible LLM backend streaming (lazy import in `growth-agent/backend/app/inference/backend.py:117`)
- cytoscape 3.29.2 - Concept graph visualization library (frontend dependency)

**Infrastructure:**
- python-multipart >=0.0.9 - Multipart form data support for FastAPI
- openai (Python SDK) - LLM-as-judge evaluation; lazy import in `growup_assess_v1/src/llm_judge.py:88`
- outlines / xgrammar - Constrained decoding backends (optional, lazily probed in `growth-agent/backend/app/inference/constraints.py:25-43`)

**Frontend:**
- react-dom 18.3.1 - React DOM renderer

## Configuration

**Environment:**
- Python `os.getenv()` reads from environment variables or `.env` file
- `.env.example` present at `growth-agent/backend/.env.example` (documents required vars, does not contain secrets)
- All four subproject variants (`growth-agent`, `growth-agent-harness`, `growth-agent-ref-impl`, `growth-agent-integrated`) have identical `.env.example` files

**Key configs required:**
- `DATABASE_URL` - SQLAlchemy async connection string (default: `postgresql+asyncpg://dev:dev@localhost:5432/growth_agent`)
- `CONCEPT_SENTINEL` - Sentinel string separating prose from structured JSON in LLM output (default: `≡≡CONCEPT_BLOCK≡≡`)
- `INFERENCE_TIMEOUT_S` - Overall LLM call timeout (default: 60)
- `FIRST_TOKEN_TIMEOUT_S` - First token timeout (default: 5)
- `JSON_PARSE_TIMEOUT_S` - Structured JSON parse timeout (default: 15)
- `MAX_EXPLORE_DEPTH` - Drill-down depth limit (default: 6)
- `MAX_CONCEPTS_PER_SESSION` - Concept count limit per session (default: 200)
- `BACKFILL_MAX_RETRY` - Async backfill retry count (default: 3)
- `THRESHOLDS_PATH` - Path to thresholds JSON file (default: `app/thresholds.local.json`)
- `LLM_BASE_URL` - OpenAI-compatible API base URL (enables real LLM backend; absent = stub)
- `LLM_API_KEY` - API key for LLM backend
- `LLM_MODEL` - Primary model name (default: `gpt-4o-mini`)
- `LLM_BACKUP_MODEL` - Failover model name
- `TELEMETRY_DIR` - Directory for QAStep telemetry NDJSON files (default: `telemetry/qa_steps`)
- `OPENAI_API_KEY` - Used by evaluation pipeline LLM-as-judge (`growup_assess_v1/src/llm_judge.py`)

**Build:**
- `growth-agent/frontend/vite.config.js` - Vite config with proxy to backend (`/qa` and `/concept` -> `http://localhost:8000`)
- `growth-agent/backend/pytest.ini` - pytest config (`asyncio_mode = auto`, `testpaths = tests`)
- `growth-agent/backend/app/thresholds.local.json` - Normalization threshold values (hot-reloadable)
- `growth-agent/docker-compose.yml` - Docker Compose for PostgreSQL + backend + frontend

## Platform Requirements

**Development:**
- Python 3.10+ with pip
- Node.js 18+ with npm
- PostgreSQL 16 (or SQLite via `aiosqlite` for local dev)
- Docker Desktop (optional, for `docker-compose.yml`)

**Production:**
- Docker containers (PostgreSQL 16 image, backend build from `./backend`, frontend build from `./frontend`)
- ASGI server (uvicorn) on port 8000
- Vite preview / static build on port 5173
- PostgreSQL 16 with `pgcrypto` extension (for `gen_random_uuid()`)

---

*Stack analysis: 2026-08-11*
