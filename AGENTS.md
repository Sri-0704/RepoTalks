# AGENTS.md

This file is the operating guide for coding agents working in this repository. It applies to the entire project unless a more specific `AGENTS.md` is added in a subdirectory.

## Project at a glance

RepoTalks AI is a modular monolith for learning and discussing a software repository:

- `frontend/`: Next.js 14 App Router, React 18, TypeScript, Tailwind CSS, and a static export.
- `backend/`: FastAPI application and service modules.
- AI provider: Google Gemini for generation, streaming, multimodal requests, and embeddings.
- Persistence: SQLite plus JSON metadata and extracted repository files under `DATA_DIR`.
- Production shape: one service. FastAPI serves the exported files from `frontend/out` and owns the `/api/*` routes.

Do not describe the feature service classes as independently deployed microservices. Architecture diagrams and code traces are source-assisted model output, not runtime traces or a proven complete call graph.

## Start here

Read these files before making a broad change:

1. `README.md` for the user-facing capabilities and local setup.
2. `backend/main.py` for API contracts and request orchestration.
3. `backend/config.py` and `.env.example` for runtime configuration.
4. `frontend/lib/api.ts` and `frontend/context/ProjectContext.tsx` for client/server and project-state contracts.
5. The relevant service and its tests.
6. `AUDIT_REPORT.md` for historical security context and `LATENCY_IMPLEMENTATION_PLAN.md` for planned performance work. Verify their claims against current code before treating them as current defects or completed work.

Do not read or modify generated or local-state trees unless the task specifically requires them: `venv/`, `.venv/`, `frontend/node_modules/`, `frontend/.next/`, `frontend/out/`, `data/`, `output/`, `__pycache__/`, and `.pytest_cache/`.

## Repository map

### Backend

- `backend/main.py`: FastAPI app, request models, routes, SSE responses, session scoping, and static frontend serving.
- `backend/config.py`: Pydantic settings and data-directory initialization. Treat it as the configuration source of truth.
- `backend/services/ingestion_service.py`: GitHub clone and ZIP ingestion, source parsing, staging, promotion, and cleanup.
- `backend/services/project_service.py`: per-session project registry, active project, ownership checks, and project deletion.
- `backend/services/vector_store.py`: SQLite vector/FTS index, local and semantic retrieval, matrix caching, staging checkpoints, and index promotion.
- `backend/services/gemini_service.py`: Gemini clients, generation/streaming/embedding calls, batching, retry classification, and provider error handling.
- `backend/services/job_service.py`: durable ingestion job state, progress SSE, cancellation, recovery, and ephemeral API-key storage.
- `backend/services/context_builder.py`: bounded model context and citations.
- `backend/services/{viva,tracer,architecture,audience}_service.py`: feature-specific prompts and response parsing.
- `backend/services/{cache_service,rate_scheduler,timing}.py`: caches, embedding pacing, and request timing.
- `backend/test_server.py`: security and regression coverage for the API/service boundary.
- `backend/tests/`: focused unit, ingestion, job, retrieval, and opt-in provider-contract tests.

### Frontend

- `frontend/app/page.tsx`: repository workspace, chat, file exploration, citations, and image submission.
- `frontend/app/{viva,architecture,tracer,audience,settings}/page.tsx`: feature pages.
- `frontend/lib/api.ts`: API base URL, headers, JSON errors, REST calls, and SSE parsing. Put new backend calls here.
- `frontend/lib/storage.ts`: browser persistence for the Gemini key and compact active-project metadata.
- `frontend/context/ProjectContext.tsx`: project list and active-project state.
- `frontend/hooks/useAbortableRequest.ts`: abort and stale-request handling.
- `frontend/components/`: shared interaction and visualization components.
- `frontend/next.config.js`: static export configuration used by the unified deployment.

### Deployment and plans

- `Dockerfile`: multi-stage frontend build plus the Python runtime image.
- `render.yaml`: Render service and persistent disk settings.
- `DEPLOY.md`: deployment instructions.
- `*_PLAN.md` and `ANTIGRAVITY_*.md`: proposals and implementation prompts, not proof that every item is implemented.

## Local setup and run commands

Run commands from the repository root unless noted otherwise. Do not rely on the checked-in `venv/`; virtual environments are machine-specific and ignored by Git.

### Backend

PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload --reload-dir backend
```

The test tools are not currently declared in `backend/requirements.txt`. For a development environment, install them explicitly:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest pytest-asyncio
```

### Frontend

```powershell
Set-Location frontend
npm ci
npm run dev
```

The frontend development server uses port 3000. `frontend/lib/api.ts` targets port 8080 during local development unless `NEXT_PUBLIC_API_URL` overrides it.

### Unified production-like run

```powershell
Set-Location frontend
npm ci
npm run build
Set-Location ..
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080`. The backend serves `frontend/out` only when that directory exists.

## Validation

Choose checks in proportion to the change, and report exactly what ran. A local result does not prove live Gemini, GitHub cloning, browser speech/media behavior, Render deployment, or production load.

### Fast checks

Backend import smoke test:

```powershell
.\.venv\Scripts\python.exe backend\test_backend.py
```

Frontend type check:

```powershell
Set-Location frontend
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

### Offline backend suite

```powershell
.\.venv\Scripts\python.exe -m pytest backend\test_server.py backend\tests\test_ingestion_quota.py backend\tests\test_job_service.py backend\tests\test_vector_store_unit.py backend\tests\test_llm_gateway.py backend\tests\test_groq_service.py backend\tests\test_generation_retry_repair.py backend\tests\test_embedding_retry_repair.py backend\tests\test_architecture_repair.py backend\tests\test_audit_remediations.py -q
```

Tests that touch global services must isolate `DATA_DIR` and create required `db`, `repos`, `uploads`, `staging`, and session directories before importing modules that instantiate service singletons.

### Frontend production check

```powershell
Set-Location frontend
npm run build
```

The build performs the relevant Next.js compilation, lint/type validation, and static export. Check the route table and build output for new warnings.

### Live provider contract test

This is opt-in because it uses a real API key and consumes quota:

```powershell
$env:RUN_LIVE_GEMINI_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest backend\tests\test_embedding_contract.py -v
```

Never enable this implicitly in a general test run. Never print or commit the key.

## Architecture and data flow

The main request path is:

```text
Next.js UI
  -> frontend/lib/api.ts
  -> FastAPI /api routes in backend/main.py
  -> project authorization and feature services
  -> local repository metadata / SQLite retrieval
  -> Gemini only when the requested feature needs it
```

Ingestion is a staged workflow:

```text
GitHub URL or ZIP
  -> safe staging directory
  -> parse files and symbols
  -> batch embeddings with bounded retries
  -> write a new index generation
  -> promote repository and register it to the browser session
  -> clean temporary state
```

Preserve the prior usable repository/index until replacement promotion succeeds. Failed ingestion must clean partial state without deleting another session's project.

## Backend rules

- Keep `/api/*` routes in `backend/main.py`; put substantial behavior in a service module.
- Use Pydantic request models and explicit HTTP status codes. Preserve `GeminiServiceError` subclasses instead of converting provider failures into fabricated success data.
- Use the asynchronous Gemini client for provider calls. Move blocking filesystem, Git, CPU, or synchronous library work off the event loop with `asyncio.to_thread` when it can stall requests.
- Bound model context, chat history, uploads, extracted bytes, archive members, source-file sizes, and image sizes.
- Authorize a `repo_id` against the current session before reading, selecting, or deleting project data. The anonymous opaque session ID is isolation, not authentication.
- Treat all archive member names, upload names, repository paths, and static paths as untrusted. Resolve and verify containment before writing or serving a path.
- Keep project metadata writes atomic. Use opaque `repo_<uuid>` IDs rather than deriving storage identity from a user-controlled name or URL.
- Never persist user-supplied Gemini keys. The job service's key store is intentionally in memory only.
- Keep SQLite transactions short. Preserve WAL/busy-timeout behavior unless measurements justify a change.
- Keep embedding model, dimensionality, content hash, and index generation compatible. Never mix incompatible vectors in one searchable index.
- Local file/symbol/keyword search must remain usable without a Gemini request. FTS5 may fall back to bounded `LIKE`; make fallback behavior diagnosable.
- In SSE endpoints, emit valid `data:` frames, terminate predictably, propagate cancellation, and prevent proxy buffering where appropriate.

## Frontend rules

- Keep TypeScript strict and avoid introducing `any` when a stable API response can be typed.
- Centralize network behavior in `frontend/lib/api.ts`. Preserve `X-Session-Id`, optional `X-Gemini-API-Key`, abort signals, and readable non-JSON error handling.
- Scope feature state by `activeRepo.repo_id`. When the repository changes, clear results that belong to the previous repository and abort stale requests.
- Use `useAbortableRequest` or an equivalent `AbortController` pattern for replaceable or long-running requests. Do not let an older response overwrite newer state.
- Do not duplicate large repository trees or file contents in `localStorage`; store only compact metadata. Treat the browser-stored API key as a BYOK convenience, not secure credential storage.
- Maintain keyboard access, visible focus, labels, empty/loading/error states, and reduced-motion behavior when changing interactive components.
- Keep pages compatible with `output: 'export'`: do not add server-only Next.js features unless the deployment architecture changes too.
- Reuse the existing design tokens and shared shell/components before adding page-specific styling.

## Security and reliability invariants

Changes are incomplete if they break any of these:

- Project ownership is checked before destructive work.
- ZIP extraction and static-file serving cannot escape their intended roots.
- A failed provider call is reported honestly; it must not generate a fake score, trace, citation, or architecture claim.
- A failed re-index does not destroy the last usable index.
- Embedding output count and dimensions are validated before promotion.
- API keys and `.env` contents do not enter logs, SQLite, generated docs, fixtures, or commits.
- Cancellation and cleanup paths are tested, especially during embedding retry/backoff.
- Generated directories, databases, caches, and build artifacts are not committed.

## Configuration

- Copy `.env.example` to `.env` for local values; never commit `.env`.
- `backend/config.py` is authoritative for defaults and validation. Keep `.env.example`, deployment files, and documentation synchronized when adding or changing a setting.
- `DATA_DIR` contains mutable runtime state. Tests should point it at a disposable temporary directory, and deployment changes must preserve the Render disk mount contract.
- `NEXT_PUBLIC_API_URL` is build-time public frontend configuration. Do not put secrets in any `NEXT_PUBLIC_*` variable.

## Change discipline

- Inspect nearby tests and callers before editing a public API, response shape, persisted schema, or storage path.
- Keep changes narrowly scoped. Do not rewrite generated output or unrelated plans/reports.
- Add a regression test for every bug fix when the behavior is testable.
- For schema or index changes, define compatibility, invalidation/migration, rollback, and cleanup behavior before implementation.
- For performance work, measure the full request path and separate provider latency from local retrieval, serialization, rendering, and cold-start time.
- Update `README.md`, `.env.example`, `DEPLOY.md`, and API types when user-facing behavior or setup changes.
- This checkout may not contain Git metadata. Check `git status` before relying on history, branches, diffs, commits, or rollback commands.

## Completion checklist

Before handing work back:

1. Review the diff or, without Git metadata, re-read every changed file.
2. Run the smallest relevant checks plus broader checks for shared contracts.
3. State which checks passed, failed, or could not run and why.
4. Call out anything that still requires a real Gemini key, browser/device, remote GitHub repository, Docker/Render environment, or load test.
5. Do not claim an AI-generated diagram or trace is runtime-verified unless a separate runtime instrument actually produced it.
