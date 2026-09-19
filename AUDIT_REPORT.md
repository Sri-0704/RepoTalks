# RepoTalks AI: code and interview-readiness audit

Initial audit reviewed September 16, 2026. This report preserves the original findings and evidence. The remediation pass below modified the application source; audit scripts and disposable fixtures remain in `.audit/`.

## Remediation status — September 16, 2026

The critical and high-severity implementation findings in this report have been addressed: generated opaque repository IDs; isolated staging and rollback on imports; safe archive extraction with byte/file limits; ownership checks before deletion; safe static-file containment; supported embedding configuration with model/dimension metadata; explicit Gemini failure states; offloaded cloning and vector scoring; atomic project metadata writes; and durable Render storage configuration.

The frontend now defaults to backend port 8080 during local development, submits image attachments to the multimodal endpoint, sends bounded chat history, shows citations, prevents concurrent sends, and clears repository-scoped feature state. Documentation, deployment setup, and the stale backend test import were corrected. The report's historical descriptions should therefore be read as the pre-fix snapshot, not current behavior.

The project deliberately still uses anonymous opaque browser sessions rather than user accounts. A public shared deployment using a server-owned Gemini key should add authentication, quotas, and rate limiting. Architecture maps and execution traces remain source-assisted analysis rather than verified runtime execution or a complete call graph.

**Assessment:** a useful foundation for a resume project, with enough substance to discuss ingestion, retrieval, streaming, state management, and failure handling. The present implementation has reproducible data isolation and filesystem vulnerabilities, misleading AI fallback behavior, and several incomplete features. Fix those before a shared deployment or a live interview demonstration. Describe your own additions and measurements accurately.

## What the implementation actually does

1. Next.js/React imports a GitHub repository or ZIP through FastAPI.
2. Ingestion scans allowed text extensions. Python symbols use `ast`; JavaScript/TypeScript symbols use regex. Other supported extensions are read as text without symbol extraction.
3. Files are split into non-overlapping 40-line chunks. This is not AST-aware chunking.
4. Gemini is asked to embed the chunks. Failures silently substitute local hashed word vectors.
5. SQLite stores chunk text, metadata JSON, and embedding JSON. Project/session metadata lives separately in JSON files; repository content lives on disk.
6. Search combines file/symbol matching, SQL substring matching, and a Python cosine-similarity scan over every vector in the project. There is no approximate-nearest-neighbor vector index; NumPy is declared as a dependency but not used for scoring.
7. Retrieved snippets are inserted into Gemini prompts. Chat and tracer narration stream SSE; viva, audience explanations, and architecture return parsed JSON.
8. Architecture uses file/import heuristics plus LLM output. Tracing asks an LLM to infer steps from retrieved snippets; it does not execute code or construct a verified interprocedural call graph.

This is a modular monolith with a RAG pipeline. Feature-specific service classes are not independently deployed microservices. An instruction calling the model an autonomous agent does not itself implement tool use, iterative exploration, or verified static analysis.

## Highest-priority findings

### 1. Critical: deployed static route can read outside the frontend export

**Evidence:** `backend/main.py:550-565`. `full_path` is joined directly onto `FRONTEND_OUT_DIR`, then passed to `FileResponse` without canonical-path containment checks.

**Reproduced:** registered the exact route implementation against a disposable export directory. An encoded parent-directory request returned HTTP 200 and the contents of an audit marker outside that directory. This was tested locally, not against any deployed site.

**Impact:** files readable by the service process can be exposed when the static export route is enabled. Repositories, metadata, databases, and configuration are potentially affected according to filesystem layout and permissions. This turns a deployment convenience into an unauthenticated file-read boundary failure.

**Fix:** use a containment-enforcing static file handler or resolve every candidate and require it to remain inside the export root. Apply the same rule to HTML/index fallbacks and symlinks. Regression test encoded traversal using harmless fixtures.

### 2. Critical: upload filename controls the output path

**Evidence:** `backend/main.py:218-223`.

**Reproduced:** multipart filename `../audit_marker.txt` overwrote a disposable file outside `uploads`. The request then returned 500, but the overwrite had already occurred.

**Impact:** a rejected/non-ZIP upload can still overwrite writable server files. Normal browser filename behavior is not a server-side security check.

**Fix:** assign a generated temporary filename, treat the client name as display metadata, validate the archive before promotion, and clean up failures in `finally`. Never incorporate raw client paths into repository storage IDs.

### 3. High: two sessions importing the same name share source files and vectors

**Evidence:** `backend/services/ingestion_service.py:27-53`, `backend/services/vector_store.py:170-189`.

**Reproduced:** two sessions uploaded different code under `project.zip`. Both received the same repository ID; the first project's source file and index were replaced with the second upload's content. Project ownership metadata remained separate, so it did not protect storage.

GitHub IDs also derive from the URL and Python's process-dependent `hash()`, with only 10,000 suffix values. IDs vary across restarts and are not durable identifiers.

**Fix:** use opaque persistent project IDs, explicit owners, and isolated storage paths. If deduplicating source deliberately, store immutable content snapshots and maintain access-controlled references. Build new imports in staging and promote only after successful indexing; current re-import deletes old source before clone/extraction succeeds.

### 4. High: unauthorized deletion destroys the vector index before returning 404

**Evidence:** `backend/main.py:180-187`.

**Reproduced:** an unrelated session deleted a known test repository ID. The endpoint returned 404, but its index count fell from one to zero.

**Fix:** validate ownership before any side effect, and use the same owner-scoped identifier throughout metadata, files, and vectors. Add cross-session negative tests. Existing isolation tests use different repository IDs and miss the collision path.

### 5. High: ingestion lacks resource limits and URL restrictions

**Evidence:** `backend/config.py:12`, `backend/main.py:215-223`, `backend/services/ingestion_service.py:27-82`.

**Reproduced:** a valid archive still succeeded with `MAX_UPLOAD_SIZE_MB = 0`. A local filesystem repository path was accepted and forwarded to the clone operation in a mocked-clone probe.

The service accepts arbitrary non-empty clone strings, extracts archives without total-uncompressed-size/file-count limits, reads whole files, and runs cloning inline without an application-defined deadline. These permit disk/memory exhaustion and server-side access to unintended clone destinations. Repository file symlinks are not explicitly rejected or checked for containment before reading; this warrants a Linux regression test.

**Fix:** enforce compressed and expanded byte budgets, per-file and file-count limits, allowed archive types, HTTPS GitHub owner/repository URL rules, clone deadlines, and resolved source-path containment. Add admission control for expensive operations. Do not assume every use of Python `extractall` proves ZIP traversal; the demonstrated traversal here is the outer upload filename.

### 6. High: provider failures are presented as successful assessment

**Evidence:** `backend/services/viva_service.py:128-135,183-191,286-383`; `backend/services/tracer_service.py:101-120`; `backend/services/audience_service.py:70-83`.

**Reproduced with provider disabled:** an answer of “I do not know” received 7.5/10; a calculator trace included nonexistent `process_request`; audience explanation described RepoTalks instead of the calculator repository.

The fallback question bank invents implementation details and first-person development history: AST-aware chunking, query caching, transactional metadata, performance measurements, and other unimplemented behavior. This is especially harmful in an interview-preparation product.

**Fix:** return explicit unavailable/degraded states. Never synthesize grades, code locations, or development achievements on failure. Use typed structured output, validate required fields and score ranges, and check file/symbol references against the indexed repository. Compute average grades deterministically from valid evaluations.

### 7. High: embedding configuration is obsolete and fallback vectors are unstable

**Evidence:** `backend/config.py:11`; `backend/services/gemini_service.py:112-144`; `backend/services/vector_store.py:310-317`.

Google's [current deprecation table](https://ai.google.dev/gemini-api/docs/deprecations) lists January 14, 2026 for `text-embedding-004` and recommends `gemini-embedding-2`. No authenticated provider call was made during this audit; this lifecycle finding is documentation-verified.

**Reproduced:** the same fallback text populated entirely different dimensions under Python hash seeds 1 and 2. Persisted vectors therefore become incompatible with newly generated queries across process seeds/restarts. Switching between fallback and provider embeddings also mixes unrelated vector spaces. The cosine function silently truncates different dimensions instead of rejecting incompatibility.

**Fix:** configure a supported model; persist model/version/dimension with every index; re-index on migration. If offering offline retrieval, use a stable hash or an explicitly separate lexical search mode and disclose it in the UI. Never compare provider vectors with fallback vectors.

### 8. High for concurrency: async routes contain blocking work

**Evidence:** `backend/services/gemini_service.py:57,100-107,118`; `backend/main.py:196`; `backend/services/vector_store.py:262-282`.

**Reproduced:** a mocked 200 ms synchronous provider call delayed a 10 ms event-loop heartbeat to approximately 231 ms. Declaring a function `async` does not make the synchronous SDK calls asynchronous.

Cloning, extraction, scanning, SQLite calls, and Python vector scoring also run inline. One import or slow provider stream can hold up other requests in the same worker.

**Fix:** use the SDK's async interface, bounded concurrency/timeouts, and suitable thread/process offloading. Long ingestion should have a job state and progress endpoint. A queue can follow when measured load justifies it.

## Functional and retrieval defects

| Finding | Evidence and effect | Repair |
|---|---|---|
| Local development requests go to port 3000 | `frontend/next.config.js:9` injects an empty API URL; `frontend/lib/api.ts:4` accepts it. Executing the actual API client produced `/api/chat/stream` in development. | Default development to port 8080, or supply a development proxy; keep same-origin production deliberate. |
| Image upload UI is disconnected | `frontend/app/page.tsx:45,113-128,270` stores the image but only invokes text streaming. A backend multimodal endpoint exists. | Add a multipart API client and route selected images through it; show submission state and clear attachments appropriately. |
| Chat has no conversational memory | `frontend/lib/api.ts:151` sends no history; `backend/main.py:79` declares history but the generation code never uses it. | Store/send bounded history per conversation and repository; include it in retrieval and generation where appropriate. |
| Structured citations are discarded | `frontend/app/page.tsx:127` passes no citation handler. | Store citations on each assistant message and expose actual file/line evidence. Retrieved candidates alone do not prove every generated claim. |
| Enter bypasses the streaming lock | `frontend/app/page.tsx:99-100,118-123,292-297`. Only the button checks `isStreaming`; Enter invokes the unguarded handler. Concurrent callbacks overwrite the last message. | Guard the handler, track stable message/request IDs, and support cancellation. |
| Results remain attached to the previous project | Overview, viva, audience, architecture, and tracer retain their result state on active-project changes. Viva resets only practice flags (`frontend/app/viva/page.tsx:85-98`); architecture updates only DB-tab visibility (`frontend/app/architecture/page.tsx:31-41`). | Key results by repository ID or reset on change; abort/ignore late responses. Otherwise old questions can be graded against the newly selected repository. Source-confirmed; no browser reproduction was performed. |
| Voice transcription loses earlier sentences | `frontend/components/VoiceRecorder.tsx:37-43` rebuilds text starting at `event.resultIndex`. Executing the callback with two recognition events retained only the second sentence. | Accumulate final segments or reconstruct all current recognition results. Stop recognition and speech on unmount. |
| File tree breaks on Windows | Ingestion stores Windows backslashes at `ingestion_service.py:72`, but tree/architecture split only `/`. A nested path became one flat filename in the probe. | Store all relative paths with `as_posix()` and normalize at boundaries. |
| Underscore-prefixed files disappear | `ingestion_service.py:222` filters all keys starting `_`; `__init__.py` and `_helper.py` disappeared in the probe. | Separate internal metadata from child names or filter only actual reserved metadata keys. |
| Context has no total budget and chunk order is wrong | `vector_store.py:137,286-308`: exact matches return entire files, independent of `top_k`; string IDs sort `0,1,10,11,12,2...`. Probe requesting one result returned thirteen. | Store integer chunk ordinals and line ranges; rerank and impose a shared token budget. Preserve necessary neighboring chunks deliberately. |
| Relevance scores overstate evidence | Exact file/symbol matches get 1.0; substring matches get 0.85. `backend/main.py:352` further bypasses low-similarity rejection for common words like “what” and “how”. | Separate match type from semantic similarity and evaluate answerability using actual evidence. Treat code/comments as untrusted prompt data, not instructions. |
| Architecture draws unsupported edges | `architecture_service.py:285-313` links pages/services/databases by filename categories and iteration order. These edges are merged into results even when the model succeeds. | Mark heuristics/inferences explicitly; build verified import/call edges with provenance. Do not label arbitrary connections as traced data flow. |
| Several API helpers ignore HTTP errors | `frontend/lib/api.ts:199-236,255-276,324-334` calls `res.json()` without testing `res.ok`. | Centralize typed status/error handling; validate provider JSON before returning it. An error object can otherwise be rendered as feature data. |

## Deployment, persistence, and documentation

- **Docker lacks an explicit Git installation.** `Dockerfile:11-17` uses `python:3.11-slim` and installs Python dependencies but no Git executable. [GitPython requires Git](https://gitpython.readthedocs.io/en/stable/intro.html). GitHub cloning, and potentially GitPython import/startup with its default refresh behavior, fail without it. Docker was not built during this audit; add Git and verify from a clean container.
- **Render storage is not durable as configured.** `render.yaml` declares no disk; `settings.DATA_DIR` is fixed to local application storage. Render documents that [local changes are lost across restarts/redeploys without a persistent disk](https://render.com/docs/disks). Configure a persistent mount plus configurable data path, or external persistence. Verify metadata, source, and vectors together after redeployment.
- **Project metadata writes are non-atomic and errors are swallowed.** `project_service.py:27-43` reads/writes a whole JSON registry without transactional protection. A crash can leave invalid JSON that loads as an empty project list; a write error can still lead to a success response. Use transactions or atomic replacement and explicit failures. Concurrent worker write-loss was not runtime-tested.
- **SQLite connections lack explicit closure.** `vector_store.py:18-24` and its callers use a connection context manager for transaction handling without an explicit close lifecycle. Prefer a context manager that closes in `finally`, especially for repeated requests and Windows file handles.
- **Sessions are anonymous browser credentials, not user accounts.** `backend/main.py:41-59` trusts a supplied session string, and the frontend stores it in localStorage. There is no login, server-side ownership identity, expiry enforcement for header sessions, or per-user provider budget. This matters if deploying with a shared server Gemini key. Use an appropriate account/session design for the deployment scope and enforce quotas.
- **Setup instructions and feature claims are stale.** `.env.example` describes OpenRouter/Chroma. `Settings` is a Pydantic `BaseModel` and does not itself load `.env` files. A Pinecone setting is displayed (`frontend/app/settings/page.tsx:268`) but is not consumed by the backend. The documented health dashboard has no service, route, or frontend page. The active architecture page renders React Flow, not `MermaidViewer`.
- **README overstates language parsing.** Only Python uses an AST; JS/TS regex symbols use line 1; Java/Go/Rust/C/C++ have no symbol parsing. A Java sample returned empty symbols. Do not claim multi-language AST indexing until implemented.
- **Dependency reproducibility differs by layer.** The frontend has a lockfile; backend requirements use open-ended lower bounds. The audit used Python 3.13 and currently resolved dependencies, not the documented Docker Python 3.11 environment. Pin/test a supported deployment environment and install from a reproducible lock.
- **No Git metadata or license file is present in this supplied folder.** The README credits upstream authors and claims MIT. Establish repository history and preserve provenance; do not claim another author's implementation as original work.

## Verification

- All 12 backend Python files parsed successfully.
- Installed frontend dependencies using the existing lockfile. TypeScript passed: `node node_modules/typescript/bin/tsc --noEmit --incremental false`.
- The Next.js production build passed, including compilation, type validation, and generation of all 10 static pages. Build success does not validate browser interactions or live provider behavior.
- Full backend test collection failed at `backend/test_backend.py:14` with `ModuleNotFoundError: backend.services.dashboard_service`.
- The six tests in `backend/test_server.py` passed with Gemini access explicitly mocked/disabled and storage redirected to disposable audit directories. This is not proof of working live AI behavior.
- Offline probes reproduced storage collisions, unauthorized index deletion, upload traversal, static file traversal, ignored upload limits, fabricated fallback outputs, hash-vector instability, Windows tree flattening, hidden underscore files, missing Java symbols, unbounded exact retrieval, chunk ordering, and event-loop blocking.
- Frontend code probes verified the default relative API URL, missing chat history, and lost speech segments.
- No live Gemini calls, public deployment testing, actual remote clones, microphone tests, or browser interaction tests were performed. No dangerous archive or real secret was used. Static-route traversal used a disposable marker and the actual extracted route implementation.
- Raw evidence: `.audit/results.json`, `.audit/frontend-results.json`; reproducible scripts: `.audit/run_audit.py`, `.audit/frontend_checks.cjs`.

## Practical implementation order for a resume project

1. **Make data handling trustworthy:** fix static/upload paths, owner isolation, deletion order, safe re-import, and input/resource limits. Prove it with two-session and malformed-upload tests.
2. **Make AI failures honest:** remove fabricated grades/answers/traces; migrate embeddings with explicit index versioning; validate structured responses.
3. **Make one complete demo reliable:** import a small known repository, ask a cited question and follow-up, switch projects, run one viva question, and show a supported architecture relation. Fix routing, stale state, image submission, and streaming races.
4. **Measure retrieval quality:** create 20-30 repository questions with known supporting files/lines, including questions whose answers are absent. Record retrieval recall@k, citation correctness, abstention behavior, and latency. Compare fixed-line chunking against symbol-aware chunking on the same cases.
5. **Verify operations:** clean install/build, provider-error tests, restart persistence, Docker startup, and a small concurrency test. Add CI and update the README to precisely match observed behavior.

The strongest interview story is a measured engineering change: “I found that two uploads with the same filename shared storage, redesigned ownership and IDs, and added a regression test,” or “I evaluated retrieval on a labeled question set and measured the effect of chunking.” Add numbers only after collecting them.

Be prepared to explain: the import transaction; why metadata and vectors must share ownership boundaries; embeddings and cosine similarity; lexical versus semantic retrieval; token budgets; SSE cancellation/error handling; blocking I/O in async routes; SQLite trade-offs; provider failure states; and the difference between inferred diagrams and verified execution graphs.
