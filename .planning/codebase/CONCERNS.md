# Codebase Concerns

**Analysis Date:** 2026-08-11

## Tech Debt

**Duplicate project directories (5 copies of overlapping code):**
- Issue: The repo root contains 6 top-level directories that are near-duplicate or variant copies of the same project: `growth-agent/`, `growth-agent-harness/`, `growth-agent-integrated/`, `growth-agent-ref-impl/`, `growth_inference_client/`, and `growup_assess_v1/`. Only `growth-agent/` is the superset (76 Python files); the others are subsets or intermediate snapshots (45/36/26/12/14 files respectively). `growth-agent/backend/app/harness/` and `growth-agent-harness/backend/app/harness/` are byte-identical. `growup_assess_v1/` and `growth-agent/eval/` have identical source files. All committed in a single `87d0e25` commit.
- Files: Entire repo structure under `growth-agent-harness/`, `growth-agent-integrated/`, `growth-agent-ref-impl/`, `growth_inference_client/`, `growup_assess_v1/`
- Impact: Massive code duplication; any fix in one copy must be manually propagated; 61 `.pyc`/`__pycache__` files committed (see Security); total tracked files: 352, of which ~60% are duplicates; makes grepping and navigation unreliable; future Claude instances may edit the wrong copy.
- Fix approach: Delete `growth-agent-harness/`, `growth-agent-integrated/`, `growth-agent-ref-impl/`, `growth_inference_client/`, `growup_assess_v1/`; retain only `growth-agent/` as canonical. If historical variants are needed, use git branches/tags instead of directories.

**Stub/mock implementations as production defaults:**
- Issue: Multiple critical components default to stub/mock implementations that will silently produce incorrect results if deployed without configuration. The `default_backend()` function returns `StubLLMBackend` when `LLM_BASE_URL` is not set; `MockEmbedder` and `MockLLMJudge` are the default embedder/judge in `ConceptNormalizer`; `backfill_processor` uses `_keyword_extract()` (a regex tokenizer, not a real model); `StubInferenceClient` is the default in `build_harness()`.
- Files: `growth-agent/backend/app/inference/backend.py:161-168`, `growth-agent/backend/app/concept/normalization.py:38-64`, `growth-agent/backend/app/inference/tasks.py:99-140`, `growth-agent/backend/app/harness/app.py:71`
- Impact: If `LLM_BASE_URL`/`LLM_API_KEY` are unset, the system silently runs on stubs producing fake answers and fake concept extractions. No startup warning or health-check failure alerts operators.
- Fix approach: Add a startup assertion in `main.py` lifespan that fails fast if `LLM_BASE_URL` is missing in production (check `ENVIRONMENT != "development"`). Emit a visible log warning when stubs are active.

**Broad exception suppression (`except Exception: pass`):**
- Issue: At least 7 instances of bare `except Exception: pass` (or equivalent `except Exception:` followed by only a `log.exception` without re-raise) in harness code, plus `# noqa: BLE001` suppressing linter warnings for broad exception catches in `inference/client.py`. These silently swallow errors in checkpoint persistence, metric collection, abort, degrade callbacks, and recovery reporting.
- Files: `growth-agent/backend/app/harness/inference_session.py:327` (`_metric` swallows all errors), `:335-341` (`_abort_live` swallows abort + aclose errors), `:313` (`_fire_degrade` logs but doesn't re-raise), `growth-agent/backend/app/harness/recovery.py:69` (`_report` swallows observability errors), `growth-agent/backend/app/harness/reannotation.py:244,264,284`, `growth-agent/backend/app/inference/client.py:107,164,182,199,210,219`
- Impact: Silent failures in telemetry/metrics mean the observability dashboard (`/harness/obs/metrics`) may report stale or zero data without any error. Aborted inference calls may leave orphaned LLM streams running. Recovery success rate may be incorrectly inflated.
- Fix approach: For metrics/abort/degrade callbacks, at minimum log at WARNING level with the qa_id and exception type. For `_abort_live`, propagate aclose errors to the caller since a failed abort means the LLM stream is still running (cost + correctness).

**Redundant sentinel detection implementations (3 copies):**
- Issue: Three separate sentinel detector implementations exist: `app/inference/protocol.py::StreamSplitter`, `app/inference/sentinel.py::SentinelDetector`, and `app/harness/sentinel.py::SentinelDetector`. They implement the same cross-chunk sentinel detection algorithm with subtle differences (buffer size: `len(sentinel)+2` vs `len(sentinel)-1` vs `len(sentinel)-1`). `StreamSplitter` is marked as "legacy/baseline" but still imported in `app/inference/__init__.py` and used by `StubInferenceSession`.
- Files: `growth-agent/backend/app/inference/protocol.py:53-116`, `growth-agent/backend/app/inference/sentinel.py:28-122`, `growth-agent/backend/app/harness/sentinel.py:22-93`
- Impact: Maintenance burden; bug fixes must be applied to 3 files; the subtle buffer-size difference could cause sentinel prefix leakage in one implementation but not others.
- Fix approach: Consolidate to a single `SentinelDetector` in `app/harness/sentinel.py` (the most precise implementation with `len(sentinel)-1` buffer). Remove `StreamSplitter` from `protocol.py` and update `StubInferenceSession` to use the shared implementation.

## Known Bugs

**`get_harness()` referenced but never defined (ImportError on route registration):**
- Symptoms: `routes_harness.py` imports `get_harness` from `app.harness.app`, but `app.harness.app` only defines `build_harness()` — there is no `get_harness` function. Any attempt to register the harness observability router or call `/harness/obs/metrics` will raise `ImportError` at import time.
- Files: `growth-agent/backend/app/api/routes_harness.py:10` (importer), `growth-agent/backend/app/harness/app.py` (definer — missing `get_harness`)
- Trigger: Starting the FastAPI app or importing `routes_harness` will fail with `ImportError: cannot import name 'get_harness' from 'app.harness.app'`. Note: `main.py` does not currently include `routes_harness` in its routers (only `qa_router` and `concept_router`), so this is a latent bug that surfaces when someone tries to wire up the harness routes.
- Workaround: None. The harness observability endpoints are completely non-functional.

**`httpx.AsyncStream` does not exist (AttributeError on real LLM call):**
- Symptoms: `OpenAICompatibleBackend.stream()` calls `httpx.AsyncStream(...)`, but httpx has no `AsyncStream` class. The correct API is `httpx.AsyncClient().stream(method, url, ...)`.
- Files: `growth-agent/backend/app/inference/backend.py:125-128`
- Trigger: Setting `LLM_BASE_URL` and `LLM_API_KEY` env vars to a real OpenAI-compatible endpoint and calling `/qa/{qa_id}/stream`.
- Workaround: The system falls back to `StubLLMBackend` if `LLM_BASE_URL` is unset, so the bug is never triggered in stub mode. But the "real LLM" path is completely broken.

**`handle()` is an async generator called as one (semantic correctness):**
- Symptoms: `InferenceSession._drain()` defines `async def handle(chunk)` with `yield` statements, making it an async generator. It's consumed via `async for ev in handle(first)`. However, `handle` contains a bare `return` (line 185) in the JSON-accumulation branch, which is valid for async generators (stops iteration). The bug is that `raise _JsonTimeout()` inside `handle` will propagate through `async for ev in handle(chunk)` but the `except _JsonTimeout` is on the outer `_drain` generator (lines 155-159), not on the `handle` call. The exception will actually propagate to the caller of `_drain` through the `yield ev` on line 218, which is the `async for ev in self._drain(...)` in `stream()` (line 144-147). The `except _JsonTimeout` at line 155 should catch it. This appears to work by accident rather than by design — the exception flows through multiple generator layers.
- Files: `growth-agent/backend/app/harness/inference_session.py:176-201,155-159`
- Trigger: A stream where sentinel is detected, JSON accumulation begins, and the JSON timeout (15s) is exceeded.
- Workaround: None needed if the exception propagation chain happens to work; but this is fragile and should be verified with integration tests that actually trigger `_JsonTimeout`.

**`except (asyncio.TimeoutError, Exception)` is logically redundant:**
- Symptoms: In `inference/client.py` lines 164 and 182, the code catches `(asyncio.TimeoutError, Exception)`. Since `asyncio.TimeoutError` is a subclass of `Exception`, this tuple is equivalent to just `Exception`. The `# noqa: BLE001` comment suggests the author was aware of the broad-catch lint rule but worked around it rather than narrowing the catch.
- Files: `growth-agent/backend/app/inference/client.py:164,182`
- Trigger: Any exception during L2 split streaming or extraction.
- Workaround: Replace with specific exception types or a single `except Exception`.

**`canAct()` function has incorrect boolean logic:**
- Symptoms: `qaStore.js` line 28: `export function canAct(qaId) { return state.inflight === null || state.inflight === qaId === false }`. The expression `state.inflight === qaId === false` is a chained comparison that evaluates as `(state.inflight === qaId) === false`, which is `state.inflight !== qaId`. So the function returns `inflight is null OR inflight is not qaId`, which is the opposite of the intended "allow action if inflight is null or inflight is this qaId".
- Files: `growth-agent/frontend/src/store/qaStore.js:28`
- Trigger: Any call to `canAct()` in the frontend.
- Workaround: The `guardAction()` function on line 31 has the correct logic and is what `TreeView.jsx` actually uses. `canAct` appears to be dead code.

## Security Considerations

**CORS wildcard in production (`allow_origins=["*"]`):**
- Risk: Any origin can make cross-origin requests to the backend API. Combined with no authentication, this means any website can call `/qa/start`, `/concept/merge`, etc.
- Files: `growth-agent/backend/app/main.py:37-39`
- Current mitigation: None.
- Recommendations: Set `allow_origins` to a specific list of trusted origins from an env var (e.g., `FRONTEND_URLS`). At minimum, disable wildcard in production via an environment check.

**No authentication or authorization on any endpoint:**
- Risk: All API endpoints (`/qa/*`, `/concept/*`, `/harness/*`) are completely unauthenticated. Anyone with network access can start QA sessions, merge/undo concepts, view observability metrics, and trigger LLM calls (incurring cost).
- Files: `growth-agent/backend/app/api/routes_qa.py`, `growth-agent/backend/app/api/routes_concept.py`, `growth-agent/backend/app/api/routes_harness.py`
- Current mitigation: None. No API key, no session token, no rate limiting.
- Recommendations: Add FastAPI dependency injection for auth (e.g., `Depends(verify_token)`). At minimum, add an `X-API-Key` header check middleware. Add rate limiting (e.g., `slowapi` package).

**SQL injection risk in JSONB alias matching:**
- Risk: `normalization.py` line 144 constructs a JSONB literal via f-string: `ConceptNode.aliases.op('@>')(f'["{name}"]')`. If `name` contains a double quote or backslash, the JSON literal is malformed. While SQLAlchemy's `.op()` with a string argument may parameterize in some configurations, the f-string construction of JSON content bypasses proper JSON serialization.
- Files: `growth-agent/backend/app/concept/normalization.py:144`
- Current mitigation: The `name` comes from LLM-extracted `ConceptItem.name`, which is Pydantic-validated as a `str` but not sanitized for JSON-special characters.
- Recommendations: Use `json.dumps([name])` to construct the JSONB literal safely, or use SQLAlchemy's `cast`/`func.jsonb_build_array` for proper parameterization.

**Committed `.pyc` / `__pycache__` files (61 files):**
- Risk: 61 compiled Python bytecode files (`.pyc`) and `__pycache__` directories are committed to git. These can leak implementation details, are environment-specific (cpython-310), and will cause import conflicts if developers use a different Python version.
- Files: `growth-agent/backend/app/__pycache__/*.pyc` (61 files across all project copies)
- Current mitigation: None. No `.gitignore` exists at the repo root or in any project subdirectory (only `.pytest_cache/.gitignore` auto-generated files exist).
- Recommendations: Add a root `.gitignore` with `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.egg-info/`, `node_modules/`, `.env`, and `*.db`. Run `git rm -r --cached **/__pycache__` to untrack.

**No `.gitignore` at any level:**
- Risk: No `.gitignore` file exists at the repo root or in any subproject. Only auto-generated `.pytest_cache/.gitignore` files exist. This means `.env` files, `node_modules/`, `__pycache__`, `.db` files, and IDE artifacts can be accidentally committed.
- Files: Repo root (no `.gitignore`)
- Current mitigation: `.env` files happen to not be tracked (verified), but there's no protection against accidental future commits.
- Recommendations: Create a root `.gitignore` immediately.

## Performance Bottlenecks

**N+1 query pattern in concept normalization:**
- Problem: `ConceptNormalizer.normalize()` calls `self.embedder.embed(name)` and `self.embedder.recall(vec, session_id, topk)` for each concept in a `ConceptBlock`. If the block has 8 concepts, that's 8 embedding calls + 8 recall queries. The `MockEmbedder.recall()` does a full-table scan (`SELECT ... FROM concept_node WHERE source != 'preset' ORDER BY canonical_name`) for each concept.
- Files: `growth-agent/backend/app/concept/normalization.py:74-123`, `growth-agent/backend/app/concept/normalization.py:43-57`
- Cause: No batch embedding API; no caching of embeddings for repeated concepts; MockEmbedder does linear scan of all concept nodes.
- Improvement path: Add a batch `embed_many(names)` method to the `Embedder` protocol; cache embeddings by name hash; replace MockEmbedder with a real vector index (pgvector or external service).

**Co-occurrence edge creation is O(n^2):**
- Problem: `QAStepRepository.link_co_occurrence()` creates edges between all pairs of concepts in a single extraction, resulting in `n*(n-1)/2` INSERT operations for `n` concepts. For a block with 8 concepts, that's 28 edges. Each edge is a separate `s.add()` call with no bulk insert.
- Files: `growth-agent/backend/app/qastep/repository.py:142-153`
- Cause: Nested loop with individual `ConceptEdge` object creation per pair.
- Improvement path: Use `s.add_all()` with a pre-built list, or use a bulk INSERT statement via `text()` or `insert().values([...])`.

**Telemetry writes are synchronous file I/O on the async event loop:**
- Problem: `emit_telemetry()` opens a file, writes a JSON line, and closes it synchronously (`with open(...).write(...)`) inside an async context. This blocks the event loop for every QAStep completion.
- Files: `growth-agent/backend/app/qastep/telemetry.py:50-54`
- Cause: Synchronous `open()` + `write()` in an async codebase.
- Improvement path: Use `aiofiles` for async file I/O, or push telemetry to an in-memory queue with a background flush worker.

**Global `asyncio.Lock` for merge serialization:**
- Problem: `_merge_lock = asyncio.Lock()` in `concept/service.py` is a single global lock for all merge/undo operations across all sessions. A merge in session A blocks a merge in session B.
- Files: `growth-agent/backend/app/concept/service.py:25`
- Cause: Single global lock instead of per-session locks.
- Improvement path: Use a dict of `asyncio.Lock` per session_id (the `WorkerPool` already implements this pattern in `reannotation.py:305-311` for normalization tasks).

## Fragile Areas

**Undo merge relies on audit log payload integrity:**
- Files: `growth-agent/backend/app/concept/audit.py:15-73`, `growth-agent/backend/app/concept/service.py:44-53`
- Why fragile: `replay_undo()` reads `merge_log.payload` (a JSONB column) and accesses `snap["survivor_id"]`, `snap["absorbed_id"]`, `snap["edges_to_repoint"]`, etc. with direct dict access (no `.get()` with defaults). If any key is missing or the payload is corrupted, the undo raises `KeyError` and the merge is irreversible. The `edges_to_repoint` list contains edge_ids that may have been deleted by subsequent operations, and the code handles this with `continue` (line 54), but there's no validation that the audit log itself hasn't been tampered with.
- Safe modification: Any change to the `snapshot` dict structure in `merge_concepts()` must be backward-compatible with `replay_undo()`. Add a schema version field to the payload.
- Test coverage: `test_reAnnotation.py` and `test_checkpoint_recovery.py` exist but don't test undo after edge deletion or payload corruption.

**Sentinel string is configurable but compiled into module-level regexes:**
- Files: `growth-agent/backend/app/inference/sentinel.py:23-25`, `growth-agent/backend/app/inference/protocol.py:23-25`, `growth-agent/backend/app/harness/sentinel.py:222`
- Why fragile: `SENTINEL = settings.concept_sentinel` and `_SENTINEL_RE = re.compile(...)` are evaluated at module import time. If `CONCEPT_SENTINEL` env var changes at runtime (e.g., via hot-reload), the module-level regexes become stale. The `Settings` dataclass is `frozen=True` and reads env vars at instantiation, so `settings.concept_sentinel` is fixed at startup, but the three separate module-level `SENTINEL` constants are evaluated at different import times and could theoretically diverge if import order changes.
- Safe modification: Never change `CONCEPT_SENTINEL` after startup. If sentinel must be configurable at runtime, refactor to read from `settings` on each `feed()` call (performance impact).
- Test coverage: `tests/inference/test_sentinel.py` tests cross-chunk detection but assumes the default sentinel.

**Optimistic lock version check is read-then-write race:**
- Files: `growth-agent/backend/app/qastep/repository.py:44-66`
- Why fragile: `transition()` reads the current `status` and `version`, validates the transition, then issues an `UPDATE ... WHERE version = cur.version`. Between the read and the write, another concurrent transaction could change the version. The `WHERE version = cur.version` catches this (rowcount=0 -> `OptimisticLockConflict`), but the error is not gracefully handled in the API layer — `routes_qa.py` doesn't catch `OptimisticLockConflict` and it will propagate as a 500 error.
- Safe modification: Add `except OptimisticLockConflict` handler in `routes_qa.py` stream endpoint, returning a 409 Conflict with a retry hint.
- Test coverage: No test for concurrent transitions on the same QAStep.

## Scaling Limits

**Single-process asyncio.Queue for backfill tasks:**
- Current capacity: `BackfillQueue` uses `asyncio.Queue` (in-memory) + DB persistence as fallback. Worker is a single `asyncio.create_task` consuming from the queue.
- Limit: If the process restarts, in-memory queue items are lost (only DB-pending tasks are recovered). No horizontal scaling — only one process can consume the queue. If the process crashes mid-task, the task is stuck in "running" status until manual intervention.
- Scaling path: The `WorkerPool` in `reannotation.py` with `SqlTaskStore` and `FOR UPDATE SKIP LOCKED` is the production-ready alternative (supports multi-worker, multi-process). But `main.py` starts the old `backfill_queue` worker, not the `WorkerPool`. Migrate `main.py` lifespan to use `build_harness().worker_pool.start()`.

**No connection pool sizing:**
- Current capacity: `create_async_engine(settings.database_url, pool_pre_ping=True, future=True)` with no explicit pool size. SQLAlchemy default is 5 connections.
- Limit: Under concurrent SSE streaming (each `/qa/{id}/stream` holds a DB session for the entire stream duration), 5 concurrent streams will exhaust the pool.
- Scaling path: Set `pool_size=20`, `max_overflow=40` in `create_async_engine()`, or use `NullPool` with an external connection pooler (PgBouncer).

## Dependencies at Risk

**httpx (used but not in requirements.txt):**
- Risk: `OpenAICompatibleBackend` imports `httpx` inside methods (`import httpx`), but `httpx` is not listed in `requirements.txt`. The import will fail at runtime when the real LLM backend is first used.
- Impact: The only real LLM backend implementation is non-functional.
- Migration plan: Add `httpx>=0.27` to `requirements.txt`, or switch to `aiohttp` (though httpx is the modern choice).

**outlines/xgrammar (probed but not installed):**
- Risk: `_probe_guided_backend()` tries to import `outlines` and `xgrammar`, neither is in `requirements.txt`. The probe always returns `None` and the system silently falls back to regex+Pydantic for JSON parsing.
- Impact: Constrained decoding (a key quality feature) is never active. The fallback regex parser (`_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)`) is greedy and will match the wrong JSON block if the model outputs multiple JSON objects.
- Migration plan: Either add `outlines`/`xgrammar` to `requirements.txt` (if the deployment environment supports them), or document that constrained decoding is disabled and improve the regex fallback to use balanced-brace matching (which `harness/sentinel.py::_extract_json_block` already does correctly — use that instead).

## Missing Critical Features

**No authentication system:**
- Problem: No user authentication, no API key validation, no session management.
- Blocks: Any production deployment; multi-user scenarios; access control to observability endpoints.

**No input validation on question text:**
- Problem: `QAStartRequest.question` is validated as `str` by Pydantic but has no length limit, no sanitization, no profanity filter. A user could submit a 1MB question that would be sent to the LLM.
- Blocks: Cost control; LLM prompt length limits; basic input safety.
- Files: `growth-agent/backend/app/schemas/__init__.py:31-34`

**No rate limiting:**
- Problem: No rate limiting on any endpoint. A single client can trigger unlimited LLM calls.
- Blocks: Cost control; abuse prevention.
- Files: `growth-agent/backend/app/main.py` (no rate limit middleware)

**No Dockerfile for backend or frontend:**
- Problem: `docker-compose.yml` references `build: ./backend` and `build: ./frontend`, but no `Dockerfile` exists in either directory.
- Blocks: Containerized deployment; `docker-compose up` will fail.
- Files: Missing `growth-agent/backend/Dockerfile` and `growth-agent/frontend/Dockerfile`

**No lockfile for frontend (package-lock.json / yarn.lock):**
- Problem: `frontend/package.json` exists but no lockfile. `npm install` will produce non-deterministic dependency versions.
- Blocks: Reproducible builds; CI/CD; dependency audit.
- Files: Missing `growth-agent/frontend/package-lock.json`

## Test Coverage Gaps

**No tests for OpenAICompatibleBackend:**
- What's not tested: The real LLM backend (`OpenAICompatibleBackend`) has zero test coverage. Its `stream()` method uses a non-existent `httpx.AsyncStream` API and would fail immediately.
- Files: `growth-agent/backend/app/inference/backend.py:99-158`
- Risk: The production LLM integration path is completely unverified.
- Priority: High

**No tests for harness observability routes:**
- What's not tested: `/harness/obs/metrics` and sub-routes are untested because `routes_harness.py` cannot even be imported (missing `get_harness`).
- Files: `growth-agent/backend/app/api/routes_harness.py`
- Risk: Observability endpoints are non-functional and untested.
- Priority: High

**No tests for concurrent optimistic lock conflicts:**
- What's not tested: No test simulates two concurrent transitions on the same QAStep to verify `OptimisticLockConflict` is raised correctly.
- Files: `growth-agent/backend/app/qastep/repository.py:44-66`
- Risk: Concurrent API calls could corrupt QAStep state silently.
- Priority: Medium

**No tests for undo after edge deletion:**
- What's not tested: `replay_undo()` handles missing edges with `continue`, but no test verifies this path.
- Files: `growth-agent/backend/app/concept/audit.py:50-59`
- Risk: Undo of a merge where edges were subsequently modified could produce an inconsistent graph.
- Priority: Medium

**No frontend tests:**
- What's not tested: The entire React frontend (`App.jsx`, `TreeView.jsx`, `ConceptGraph.jsx`, `qaStore.js`, `client.js`) has zero tests. No test framework is configured (no `vitest`, no `jest`, no test script in `package.json`).
- Files: `growth-agent/frontend/` (entire directory)
- Risk: Frontend regressions go undetected; the `canAct()` bug (Known Bugs) went unnoticed.
- Priority: Medium

**No integration test with real PostgreSQL:**
- What's not tested: All tests use in-memory stubs or SQLite. No test runs against PostgreSQL, so PG-specific features (JSONB `@>` operator, `gen_random_uuid()`, `FOR UPDATE SKIP LOCKED`, recursive CTE) are untested.
- Files: `growth-agent/backend/tests/` (all use stubs)
- Risk: PG-specific SQL may fail at runtime (e.g., `aliases.op('@>')(f'["{name}"]')` syntax may not work with asyncpg).
- Priority: High

---

*Concerns audit: 2026-08-11*
