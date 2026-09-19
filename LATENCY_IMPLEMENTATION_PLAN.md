# RepoTalk latency and responsiveness implementation plan

Date: 2026-09-16. Scope: frontend, API, ingestion, retrieval, Gemini calls, feature workflows, storage, and deployment. This is an implementation plan; application code has not been changed.

## Recommendation

Start with the retrieval path, avoidable provider calls, and request cancellation. Then move ingestion/re-indexing out of interactive requests, improve storage and caching, and tune rendering and deployment using measurements. Keep the current modular monolith initially. A vector database or more application workers should follow demonstrated capacity needs.

There are two separate outcomes to measure: actual time until a useful answer, and whether the interface remains responsive while waiting. A progress event improves feedback, but does not count as the first answer token.

## Evidence and limits

Reviewed the current backend routes and all service modules, frontend API/context/storage code, chat/search/viva/tracer/audience/architecture flows, graph and file-tree rendering, and deployment configuration. The earlier `AUDIT_REPORT.md` is partially outdated: Gemini calls already use `client.aio`, normal clone/extraction/analysis already use `asyncio.to_thread`, chat context is bounded, SQLite connections close, and persistent disk configuration exists. Preserve those improvements.

An offline probe extracts the actual current `VectorStore` class without importing the application. It uses synthetic 768-dimensional vectors in a disposable SQLite database and an immediate fake embedding provider. It reads no application data and makes no network requests.

| Synthetic index | Median vector load | Median JSON decode, cosine scoring, and sorting | One exact-file search, excluding network |
| --- | ---: | ---: | ---: |
| 1,000 chunks | 53 ms | 488 ms | 572 ms |
| 5,000 chunks | 426 ms | 3,756 ms | 5,020 ms |

Load/scoring medians use five runs; these are local microbenchmarks, not production percentiles or promised speedups. Both exact searches returned five exact matches, yet still invoked embeddings once and scored the entire index. This wasted work is directly reproduced.

The existing static export references approximately 774 KB of JavaScript on Overview and 881 KB on Architecture; local gzip totals are approximately 238 KB and 274 KB respectively. These totals count the unique scripts in each HTML document, including shared scripts. They are not all-route totals, a fresh build, or observed network transfer sizes.

Reproduction and results:

- `.audit/latency_probe.py`: `venv/Scripts/python.exe .audit/latency_probe.py`
- `.audit/latency_assets.cjs`: `node .audit/latency_assets.cjs`
- `.audit/latency-results.json`: recorded results and limitations.

No live Gemini call, browser performance trace, production load test, or deployed cold-start measurement was made. The deployment tier, user geography, realistic repository sizes, and provider quotas remain inputs to Phase 0.

## Current critical paths

| Interaction | Work currently completed before the useful result |
| --- | --- |
| Text chat | Project metadata read -> index counts/possible rebuild -> lexical search -> remote embedding -> load every vector -> Python scoring -> Gemini first token -> React/Markdown update |
| Quick search | 200 ms debounce -> project/index checks -> local files/symbols -> same remote embedding and vector scan -> return all result groups together |
| Import | Upload/clone -> scan/parse/chunk -> embed every chunk in one call -> serialize/write vectors -> promote files -> scan stack/register metadata -> return -> fetch all projects again |
| Project switch | Read project -> possible full rebuild -> update registry -> return full project -> fetch full project list again -> serialize active project to localStorage |
| Viva question bank | Four sequential retrievals, each with an embedding and full vector scan -> one complete generation of 10–15 questions and answers |
| Trace and hop | Retrieve -> generate complete trace containing narration -> generate another narration for first hop and every subsequent hop click |
| Architecture | Retrieve -> compute base graph -> generate complete JSON -> merge -> main-thread Dagre layout -> render all visible nodes and animated edges |

## Prioritized findings

| Priority | Finding and code evidence | Implementation direction |
| --- | --- | --- |
| P0 | Every search embeds and scans even when exact results fill `top_k`: `backend/services/vector_store.py:195`. Quick search waits for it: `backend/main.py:310`. | Separate local file/symbol search from semantic search; introduce an explicit file-query path and avoid provably unused work. |
| P0 | Every query fetches all vector JSON, decodes it, recomputes norms, and sorts every candidate: `vector_store.py:175–193`. Scoring is threaded, but database reads remain inline. | Store float32 vectors, cache normalized matrices by index version, use native array scoring and partial top-k, and fetch text only for selected IDs. |
| P0 | Missing index causes synchronous analysis and full embedding inside ordinary routes: `backend/main.py:164–183`. No rebuild deduplication. | Persist index status/version; one background rebuild per project/version; interactive endpoints report status immediately. |
| P0 prerequisite | `gemini_service.py:99–101` passes `contents=texts` for `gemini-embedding-2`, while ingestion expects one vector per chunk. Current provider documentation says a plain list is aggregated. Cached SDK 2.23.0 source also normalizes Embedding 2 inputs via `t_contents`. | Explicit independent `Content` inputs, supported request limits, output-count/order checks, and a small real-provider contract check before indexing changes. This is a source/documentation finding, not a live failure reproduction. |
| P1 | New Gemini client for each embedding/generation, with no explicit client closure or application deadline: `gemini_service.py:21–26,47,75,99`. | Reuse bounded credential-isolated clients; close on eviction/shutdown; enforce deadlines and concurrency budgets. |
| P1 | Four sequential retrievals for question bank: `viva_service.py:98–108`; redundant hop narration: `frontend/app/tracer/page.tsx:47–86`. | Batch or bound parallel retrieval, cache question banks, show existing narration instantly and generate expansion only on demand. |
| P1 | Session registry JSON is read repeatedly; list includes full trees/symbols and duplicates the active project: `project_service.py:38`, `main.py:157`. | Compact list endpoint, versioned detail endpoints, transactional metadata, and one fetch per needed resource. |
| P1 | Synchronous JSON writes, upload writes, database writes/reads, stack rescans, graph construction, and deletion still run in async routes. | Bound and offload these operations, with dedicated ingestion capacity so heavy jobs cannot occupy every interactive execution slot. |
| P1 | Streaming updates replace the last message and rerender Markdown; citation state is overwritten by subsequent text updates: `frontend/app/page.tsx:115–147`, `components/StreamingText.tsx`. | Stable message IDs, merged fields, buffered updates, memoized finished messages, and isolated active message state. |
| P1 | Search has debounce but no abort/stale-response guard; chat supports a signal in the helper but does not supply one. Other feature requests are not cancelled. | Common request lifecycle: cancel, deadline, generation ID, repository/version ownership, and explicit loading/error state. |
| P1 | Architecture/audience tab revisits regenerate results; no shared result cache. | Cache by repository revision and feature parameters; reuse in-flight requests; deliberate refresh control. |
| P2 | Ingestion uses `rglob` then filters, indexes generated text/lockfiles unless excluded, and chunks by 40 lines regardless of token size: `ingestion_service.py:183–220,307`. | Prune traversal; configurable exclusions and byte/token budgets; content hashes and incremental embeddings. |
| P2 | Architecture import resolution scans every file for every import: `architecture_service.py:274–291`. Dagre runs on the browser main thread: `InteractiveMindMap.tsx:171,231–366`. | Precomputed module lookup; cached base graph; lazy graph loading, memoized nodes, and worker layout when profiling justifies it. |
| P2 | One Uvicorn process serves frontend and API; no explicit immutable asset caching or compression policy: `Dockerfile`, `render.yaml`, `main.py:611`. | Verify actual headers; immutable hashed assets, compressed static delivery, SSE-safe proxy configuration; scale only after storage coordination. |

The likely largest wins are removing remote embeddings from quick search, replacing per-request vector decoding/scanning overhead, and reusing already-generated results. Exact ranking and relevance quality must be tested alongside speed.

## Proposed performance budgets

These are initial acceptance targets, not current measurements or guarantees. Calibrate on deployment-class hardware, a representative mobile device, the chosen network profile, and provider tier. Report cache hits/misses and cold/warm runs separately.

| Metric | Initial target |
| --- | --- |
| Visible acknowledgement of click/send | Under 100 ms |
| Local file/symbol search | Server p95 under 100 ms; user-visible p95 under 400 ms including debounce/network |
| Project list/select when metadata is available | Server p95 under 200 ms; cached project switch visibly updates under 300 ms |
| Local retrieval, 5,000 chunks at evaluated dimension | Warm p95 under 200 ms, excluding query embedding; cold load reported separately |
| Chat first useful answer token | Warm p50 under 2 s, p95 under 5 s, subject to measured provider contribution |
| Stop/switch action | UI stops updating within 100 ms; pending server/provider work cancelled where supported |
| Revisit cached architecture/audience/trace | Display in under 300 ms without a new generation |
| Ingestion job acknowledgement | Under 500 ms after upload acceptance; progress updates at least every second during active work |
| Browser field responsiveness | p75 INP under 200 ms; p75 LCP under 2.5 s on the agreed profile |
| Mixed workload protection | Lightweight API p95 under 300 ms and event-loop lag p95 under 50 ms with 5 interactive clients plus 1 import |

No universal full-answer or repository-import target is meaningful until output length, repository size, and provider limits are fixed. Record generation tokens/second, indexed chunks/second, and time to usable project for standard fixtures.

## Implementation sequence

### Phase 0 — Baselines and the embedding contract (1–2 engineering days)

Add request IDs and stage timing for ownership/metadata, queue wait, embedding, database load, lexical retrieval, vector scoring, prompt construction, provider first token, generation, serialization, and total duration. For streaming, measure until the final event, not merely until response headers are sent. Include input/output token counts when provided, chunk count, embedding dimension, cache outcome, and effective model ID. Never record API keys or repository text in performance logs.

Add browser marks from send/click through headers, first status, first useful token, final token, and first paint. Profile Markdown commits, file-tree expansion, graph layout, and storage writes. Benchmark production builds rather than dev mode. Track event-loop lag, process memory, queue length, timeouts, errors, and 429s.

Verify the Embedding 2 input shape against the pinned runtime SDK. Use separate `Content` objects for independent chunks, validate cardinality and ordering, and confirm the maximum request/token sizes for the selected model. The default embedding dimensionality is 3072; the synthetic measurements above deliberately use 768 and must not be presented as current production dimensions. Evaluate 768 versus 1536/3072 on retrieval quality before changing storage. Embedding 2 uses prompt instructions for retrieval tasks, not the older `task_type` field. See [Google embedding documentation](https://ai.google.dev/gemini-api/docs/embeddings).

Create small, medium, and large fixtures, approximately 500/5,000/25,000 chunks, plus a repository with many ignored/generated files. Prepare at least 30 representative questions with supporting file/line labels, including ambiguous names and absent answers. Test serial, 5-client, and 20-client workloads; treat 20 clients as a capacity probe until quotas/hardware support it.

Deliverables: benchmark scripts, baseline report, timing spans, provider contract test, and chosen hardware/model profile. Live model checks use a small explicit request budget during implementation.

### Phase 1 — Remove unnecessary waits (2–3 days; follows Phase 0)

1. Make quick file/symbol lookup independent of embeddings and index readiness. Return local results immediately; semantic expansion is a separate cancellable request after a longer pause or an explicit action. Search should work with metadata even if Gemini is unavailable. Keep an initial 150–200 ms debounce and tune from typing traces.
2. Introduce explicit retrieval modes: file/symbol, hybrid, and semantic. A known small file or symbol lookup can avoid embeddings entirely. For long files select relevant symbol/chunk ranges; do not assume the first five chunks answer every file question. In hybrid mode, semantic candidates must have room to contribute rather than always appearing after `top_k` lexical hits.
3. Reuse Gemini clients within the application lifespan using a bounded cache keyed by a server-side credential fingerprint. Do not change a shared client's key per request. Close clients on eviction/shutdown, including async transports, and keep credentials out of logs. See [SDK client lifecycle guidance](https://github.com/googleapis/python-genai#close-a-client).
4. Add total, first-token, and idle-stream deadlines plus bounded retries for transient failures within the original time budget. Honor provider retry guidance; do not retry invalid inputs or restart a partially delivered answer silently. Expose separate limits for interactive generation, embedding, and ingestion.
5. Show trace-provided `hop.narration` immediately. Make additional narration an explicit expansion, cached by trace/hop/version. Batch the four question-bank query embeddings as independent inputs, then share one matrix load; bounded parallel retrieval is an intermediate option if batch support is unavailable.
6. Add `AbortSignal` across frontend helpers, stable request/message IDs, and ignore late responses after changing repository/tab/hop or unmounting. Close readers in `finally`, handle `[DONE]` as terminal, propagate stream errors, and cancel the provider iterator on disconnect.

Acceptance: zero provider calls for local quick search; no duplicate automatic hop generation; simultaneous identical artifact requests share work; stale responses cannot repaint the new project. Provider timeouts always terminate the loading state.

### Phase 2 — Make RAG fast and bounded (3–5 days; follows contract validation)

Replace JSON vector storage with a versioned float32 representation. Load a normalized contiguous matrix per repository/index revision into a bounded LRU; normalize each query once, score with NumPy matrix operations, select candidates using partial top-k, and load text/metadata for winners only. Benchmark BLAS thread counts under concurrency to avoid CPU oversubscription. Offload cold matrix loads and remaining synchronous SQLite work; threaded Python cosine loops are not a substitute for native scoring.

Budget matrix memory explicitly: 5,000 x 768 x 4 bytes is about 14.6 MiB, and 5,000 x 3072 x 4 is about 58.6 MiB before metadata and temporary arrays. Bound total cache bytes, coalesce concurrent loads, and invalidate on index promotion/deletion. Retain a bounded cold path when the matrix does not fit. Consider ANN/vector search only if measured size, memory, or concurrency exceeds the budgets after this change.

Replace `LOWER(chunk_text) LIKE '%term%'` with FTS5/BM25, retaining explicit normalized path/symbol indexes for code identifiers and punctuation. Fuse lexical and semantic ranks with a bounded candidate pool, deduplicate overlapping chunks, and apply a single token budget. Avoid adding a remote reranker to every request; evaluate it only for a demonstrated relevance gap. [SQLite FTS5](https://www.sqlite.org/fts5.html) supports ranked full-text retrieval; verify availability in the packaged SQLite runtime.

Persist real start/end lines and symbol spans in metadata. Chunk primarily at symbol boundaries with a token/byte ceiling and limited overlap. Keep a fallback for unsupported languages and extremely long single lines. Use a common context builder across chat, multimodal, architecture, tracer, audience, and viva; current non-chat services do not all have a total context cap. Include user input/history and output allocation in the overall budget. Chat currently receives history but does not use it; if enabled, bound it without inserting another mandatory LLM summarization call.

Add caches in this order:

| Cache | Key and invalidation |
| --- | --- |
| Query embedding | Access/credential namespace, normalized query, model, dimension, retrieval instruction version; TTL and size cap |
| Retrieval | Owner/project, index revision, query, mode, context file, top-k and retrieval configuration |
| Normalized vectors | Project/index revision, embedding model/dimension/instruction version; atomic swap and byte cap |
| Generated artifact | Owner/project/revision, feature, parameters, prompt/schema/model version; store validated successes only |

Authorize before cache lookup. Do not store raw keys in cache identifiers. Do not share private answers across sessions. Avoid chat-answer caching until history, image hashes, context, and model configuration can all be represented correctly. Cache misses for the same work should join a single in-flight task.

Acceptance: warm local retrieval meets the target; quality does not materially regress on the labeled set (initial gate: recall@5 within 2 percentage points, with manual review of any lost citation); no cross-project/cache leakage; incompatible vectors cannot mix. Build a new index revision, validate it, then atomically promote it while retaining rollback capability.

### Phase 3 — Ingestion, metadata, and workload isolation (3–5 days)

Create durable job records with `queued`, `cloning/extracting`, `parsing`, `embedding`, `committing`, `ready`, `failed`, and `cancelled` states. Return `202` with a job ID after accepting input; expose authenticated progress through a status endpoint and optional SSE. HTTP upload acknowledgement cannot precede receiving/persisting the upload. Report completed files/chunks and actual stages; do not invent a percentage while cloning.

Use a bounded supervised worker for the current single-instance deployment with leases, restart recovery, cancellation, queue limits, and one active job per project/revision. Persist jobs before starting work; an untracked `BackgroundTasks` callback is insufficient. A separate worker process can isolate parsing/embedding CPU pressure. Keep request-supplied API keys transient; after restart, pause those jobs for key reattachment instead of persisting plaintext credentials. A configured server credential can support automatic resumption.

Change `ensure_project_index_loaded` to authorize and inspect readiness, never rebuild inline. Project switching and viva summary do not inherently need a live vector index. Let file browsing work when metadata is ready while semantic features clearly report indexing state.

Use directory-pruning traversal, explicit exclusions for generated output/lockfiles/self-metadata, and configurable file/total-byte/chunk budgets for both ZIP and GitHub imports. Preserve shallow clone and existing archive/path boundaries. Avoid a second recursive scan for stack detection; reuse manifest files encountered during analysis. Batch embeddings by supported input/token limits with bounded concurrency, cancellation and retries per batch, and staged writes. Hash chunk content and embedding configuration to reuse unchanged vectors within the authorized project scope; remove deleted chunks only when the new revision is promoted.

Move project metadata, index readiness/counts, and jobs into transactional tables, or use a temporary versioned metadata cache before migration. Return project summaries separately from active-project tree/files/symbols. Avoid rereading the complete session JSON for every ownership/readiness lookup. Use short transactions; evaluate WAL and a bounded busy timeout, noting that WAL still allows only one writer at a time. [SQLite WAL documentation](https://www.sqlite.org/wal.html).

Acceptance: a restart resumes or explicitly pauses jobs; repeated clicks create one import; failed rebuild preserves the previous usable revision; interactive workloads remain responsive during one import; repeated unchanged content produces no redundant embedding requests; deletion invalidates jobs and caches before results can be published.

### Phase 4 — Frontend response and rendering (2–4 days; can follow Phase 1 independently)

Create a query/cache layer keyed by repository ID and revision, with request deduplication, invalidation, and cancellation. A small typed layer or an established query library can satisfy this; choose one implementation. On selection, use the returned project and update local state once; avoid the subsequent full-list refetch. Persist only the active repository ID and small preferences in localStorage. Keep full details in memory or asynchronous IndexedDB if offline reuse is needed; do not treat a cached ID as authorization.

Extract memoized message rows and move Markdown renderer/plugin/component definitions to stable references. Batch incoming text on animation frames or a 30–50 ms interval, render the first useful chunk immediately, and flush on completion. Merge citations with text rather than replacing them. Keep completed messages stable while updating only the active message. For long responses use completed-block Markdown plus a lightweight active tail if profiling shows parsing stalls. Virtualize long chat history and large file trees only when DOM count or traces justify it; the existing tree only mounts expanded descendants.

Lazy-load the architecture canvas and closed search/image dialogs with `next/dynamic`, preserving accessible loading states. This defers heavy feature code until needed; Next already splits routes, so measure the extra benefit. [Next.js lazy loading](https://nextjs.org/docs/app/guides/lazy-loading).

The graph already uses `useMemo` for its transform; avoid repeating that work blindly. Memoize custom nodes, keep props stable, clone input graph data before adding directory metadata, cache layout by graph/direction/collapse state, and move large layouts to a Web Worker. Start large graphs collapsed; disable continuous edge animation at an evaluated threshold. Preserve user zoom/selection and cancel stale worker responses. These choices follow [React Flow performance guidance](https://reactflow.dev/learn/advanced-use/performance).

Profile broad backdrop blur, shadows, sidebar width animation, and file-tree height animation on a lower-powered device; reduce effects only where they cause expensive frames. Respect reduced motion. Review the unused `reactflow` and Mermaid dependency paths with a bundle analyzer before removal: installed dependencies do not automatically imply shipped JavaScript.

For voice, retain current interim transcripts and speech cleanup. Show listening/evaluating/speaking states immediately, cancel speech when switching projects, and measure recognition-end-to-submission and question-ready-to-speech separately. No custom speech service migration is justified by current evidence.

Acceptance: typing and Stop remain responsive during a long stream; no old results replace new ones; completed messages avoid repeated parsing; architecture load/layout does not block input; current-project data is visibly retained during background refreshes and errors are actionable.

### Phase 5 — Feature output and model budgets (1–3 days after caching/context work)

Persist validated architecture maps, audience explanations, and question banks by revision. Reopen instantly and regenerate only on explicit refresh or relevant invalidation. Build the directory/import graph once during analysis using a normalized module-to-file lookup rather than scanning every file per import. Show this graph as source-derived structure before optional AI enrichment; label inferred relationships honestly.

Reuse question-bank material for practice and optionally prefetch one next question only when its required state is available. Do not prefetch all audiences or all diagram types on every import. Show deterministic completed-question counts/scores while a viva summary narrative generates. Do not display incomplete grading JSON as a final grade.

Use model-supported structured output schemas for JSON features, plus server validation. Stream complete validated sections/items where useful rather than trying to render arbitrary partial JSON. Stream multimodal text with the same event/error/cancel protocol as chat; bound image dimensions/bytes, keep diagrams legible, and abort abandoned uploads/requests.

Replace ambiguous `use_pro` with task-specific model profiles and configurable prompt/output/thinking budgets where supported by the actual model. The checked-in config uses `gemini-3.8-flash` and `gemini-3.6-flash`, despite older README/UI labels; environment overrides can change them. Benchmark effective configured models for first-token time, completion, cost, and grounded-answer quality before switching. Start with concise defaults and offer expansion; do not claim model speed from its name.

Acceptance: revisiting cached artifacts makes no generation call; shortening output does not lose required fields or code grounding; malformed/failed generations are not cached; large features provide useful validated content or progress while waiting.

### Phase 6 — Deployment and regression gates (1–2 days, then ongoing)

Verify cache and compression headers on a real production build. Use immutable long-lived caching for content-hashed `/_next/static` assets, short/revalidated HTML caching, and optional CDN/static hosting if asset delivery is a measured bottleneck. Keep private API responses out of shared caches. Verify gzip/Brotli at the serving edge; do not apply buffering compression blindly to SSE. Stream responses need immediate flushing, appropriate no-cache headers, disconnect handling, and heartbeat events during long waits. Proxy-specific buffering controls require verification on the actual host.

Keep a minimal liveness endpoint independent of project registry reads, and separate readiness checks. Measure first request after idle/redeploy independently. If the deployed service is on Render Free, idle spin-down can dominate latency; verify the tier first. The checked-in blueprint already declares a persistent disk, so free-tier behavior must not be assumed. [Render free-service behavior](https://render.com/docs/free).

The current local session files and process-local locks are not ready for multiple workers without coordination. First complete transactional metadata, shared job leases, cache versioning, and duplicate-work protection. A Render service with an attached disk cannot scale to multiple instances; horizontal scaling would require external repository/object storage and shared metadata/jobs/vector access. [Render scaling constraints](https://render.com/docs/scaling). Benchmark vertical sizing and a small worker count before adding distributed infrastructure.

Use reproducible frontend/backend dependency installs, record Python/Node/model versions, and add performance gates to CI. Enable changes behind independent flags (retrieval engine, cache, job API, graph worker), roll out one at a time, and compare p50/p95/error/quality/memory. Preserve the prior index revision and permit disabling a faulty cache or retrieval implementation without losing projects.

## Validation and rollout checklist

- Fixed-fixture retrieval quality: file/symbol matches, ambiguity, absent answers, overlapping chunks, long files, citations, and equivalent results across old/new vector representations.
- Provider contract/failure checks: independent embeddings, input limits, output order/dimension, 429, timeout, invalid key, broken stream, and incomplete JSON. Mocked unit tests alone cannot validate the real provider contract.
- Isolation/caching checks: two sessions, identical query on different projects, project switch during stream, delete/rebuild during cache load, key rotation, and no stale artifact publication.
- Frontend checks: repeated rapid search, Stop, tab switching during generation, graph collapse/layout, long chat history, image request cancellation, and voice state cleanup.
- Jobs: process restart mid-batch, cancelled upload/import, duplicate submission, full disk, failed promotion, and recoverable previous index.
- Load: 1/5/20 clients, small/medium/large repositories, with and without ingestion; separate warm/cold cache, provider and local time, error rate and process memory. Compare equivalent successful workloads so fast failures never look like an improvement.
- Production verification: first token reaches the browser without buffering; hashed assets have correct caching; health stays responsive; persistence survives restart.

Suggested delivery order: baseline/contract -> local search, narration reuse and cancellation -> vector retrieval/caches -> jobs/metadata -> rendering/model/deployment tuning. Approximate total: 13–24 engineering days for one developer, depending on quality evaluation, migration complexity, and actual deployment results. The first useful release should fit in roughly 3–5 days and contain Phases 0–1; subsequent work should follow measured impact. These are planning estimates, not delivery commitments.
