# Antigravity prompt: fix Gemini embedding quota failures safely

Implement this backend reliability fix in the existing RepoTalk project after inspecting the current source. The immediate failure is repository ingestion returning `429 RESOURCE_EXHAUSTED` while embedding `https://github.com/srikant07-dev/DBMS_proj`.

Read the existing Antigravity plan at:
`C:\Users\admin\.gemini\antigravity-ide\brain\45d081aa-a2b5-4f76-a1b2-f5e948e75317\implementation_plan.md`

Use its useful direction, but apply all corrections below before coding. Preserve the existing Next.js 14 / React 18 / Tailwind 3 frontend, FastAPI backend, SQLite vector store, Gemini API contracts, session headers, staging/promotion flow, and current repository isolation safeguards. Do not migrate frameworks or rewrite unrelated frontend work.

## Confirmed causes

1. `backend/services/ingestion_service.py` currently includes generated dependency lockfiles because `.json` is a supported text extension. For DBMS_proj, `package-lock.json` files dominate the chunk and character count.
2. `backend/services/gemini_service.py` recursively processes embedding batches with no inter-batch pacing and no retry policy.
3. Gemini limits are project-level and may be RPM, input TPM, requests/day, or billing-tier limits. A new key from the same Google Cloud/AI Studio project does not reset project quota.

## Required implementation

### A. Exclude generated files from semantic indexing while preserving browsing

Create separate concepts for files retained in repository metadata/tree and files eligible for chunking. Do not simply `continue` before adding the file metadata if the product promises that lockfiles remain browsable.

Use case-insensitive matching with a named policy. At minimum cover:

- `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lockb`
- `poetry.lock`, `Pipfile.lock`, `uv.lock`, `Cargo.lock`, `composer.lock`, `Gemfile.lock`, `go.sum`
- generated/minified suffixes: `.min.js`, `.min.css`, `.map`, `.bundle.js`, `.bundle.css`
- existing ignored directories: `node_modules`, virtual environments, `.git`, `dist`, `build`, `.next`, IDE directories

For each retained generated file, add metadata such as `indexed: false` and `index_skip_reason: "generated_dependency_file"`. Keep it in the tree/file list if it is within current size/safety rules. Do not claim it is searchable through semantic RAG. Make the policy easy to unit test and log aggregate counts/characters skipped without logging file contents.

Do not remove ordinary `package.json`, source files, schemas, documentation, or configuration merely because they are JSON/YAML. Keep the existing path traversal, source-size, archive-size and supported-extension safeguards.

### B. Fix Gemini Embedding 2 input semantics before batching

The current code uses `gemini-embedding-2`. Google’s current documentation says multiple inputs to Embedding 2 can produce one aggregated embedding; the existing assumption that a list of `Content` objects always yields one independent vector per chunk must be verified against the installed SDK/provider response.

Choose and document one valid strategy:

- safest compatibility strategy: make one `embed_content` request per chunk for Embedding 2, protected by a global per-process limiter and pacing; or
- use an officially supported batch mechanism/API whose response cardinality and ordering are explicitly guaranteed; or
- switch to `gemini-embedding-001` only if the project intentionally accepts that model change, with an index-version migration and a clear configuration change.

Never zip results to input chunks unless the response count, order and dimensions have been validated. If the provider returns one aggregate vector for multiple inputs, fail with a precise diagnostic or use the valid single-input strategy. Add a model/input-contract test with mocked SDK responses; do not rely on a live API call to prove cardinality.

Preserve the existing embedding model/dimension metadata isolation. Never mix vectors generated with different models, task formats or dimensions in one active index. If changing model, task prefix, or dimension, version the index and keep the previous usable index until the replacement is complete.

### C. Use quota-aware batching and pacing

Do not use only a fixed batch size of 30 or 50 as the quota solution. A batch of 50 maximum-sized chunks can still exceed input TPM. Implement configurable limits such as:

- maximum inputs per request, if the selected provider strategy supports multiple inputs;
- maximum estimated input characters/tokens per request;
- minimum delay between embedding requests;
- a bounded global semaphore/lock so concurrent ingestion jobs cannot burst together.

Use conservative defaults from configuration and document that they are safety settings, not guarantees. Keep interactive Gemini requests from being starved by indexing if the service currently supports concurrent users.

### D. Retry only transient rate limits

Add a helper around the provider call that:

- recognizes structured HTTP/status information where available, with a conservative fallback to error text;
- retries transient RPM/TPM/temporary `429 RESOURCE_EXHAUSTED` responses with exponential backoff and small jitter;
- uses `Retry-After` or provider retry delay when available;
- uses a clear schedule such as approximately 2s, 4s, 8s, capped by a maximum;
- honors cancellation and timeout signals;
- logs model, batch/request number, attempt and delay, never API keys or source content;
- raises a typed `GeminiServiceError` after the retry budget is exhausted.

Do not retry invalid keys, malformed requests, unsupported models, permission errors, or a daily/project quota exhaustion that will not recover during the short retry window. Surface a distinct user-facing message for “temporary rate limit; retry later” versus “daily/project quota exhausted; wait, change project/tier, or use another authorized project key.”

Do not say that a fresh key automatically fixes this. Explain in the UI that Gemini quotas are evaluated at the Google project/tier level. A key from a different project may have different quota only when the user is authorized to use that project and configures it correctly. Never print or persist keys in logs.

### E. Preserve transactional ingestion behavior

If embedding fails after retries, the staged repository must not become the active repository and partial vectors must not be presented as a complete index. Preserve the prior active index. Ensure cleanup and job status/error details remain correct. Do not silently return success with missing embeddings.

Do not claim that an HTTP request abort cancels provider work unless cooperative cancellation is implemented in the ingestion worker. Existing job cancellation status must not be presented as guaranteed execution cancellation without checking the worker path.

## Frontend guidance

Update the ingestion error state in `frontend/app/page.tsx` only after preserving the backend error distinction. Provide:

- Retry ingestion using the retained repository input;
- Open Settings for key review;
- a concise temporary-rate-limit message;
- a separate project/daily-quota message with a link to Google AI Studio’s quota/billing page;
- preserved URL/upload context where safe;
- no API key value in the UI beyond the existing masked/reveal control.

Do not add fake percentage progress. Current ingestion may only provide truthful indeterminate progress unless job correlation and cooperative cancellation are separately implemented.

## Verification requirements

Do not hard-code “DBMS_proj must produce exactly 96 chunks.” Chunk totals depend on repository revision, chunking rules and file limits. Verify instead that:

- no excluded generated path appears in `summary["chunks"]`;
- ordinary source/config/docs paths remain present;
- retained excluded files appear in metadata/tree with `indexed: false` where applicable;
- the fixture reports skipped file/chunk/character counts;
- chunking is deterministic for a fixed fixture.

Add offline unit tests for:

1. ignore-policy matching and case normalization;
2. retained metadata versus excluded chunk output;
3. request batching by input/count budget;
4. successful embedding cardinality/order/dimension validation;
5. retry on transient 429 with mocked sleep/provider;
6. no retry on invalid key or non-transient errors;
7. cancellation during backoff;
8. no partial promotion after embedding failure;
9. concurrent ingestion serialization/rate limiting.

The existing `backend/tests/test_embedding_contract.py` performs live provider calls when a key exists. Keep it opt-in or clearly label it as an integration test; do not require a live Gemini key for the normal test suite and do not spend quota in unit tests. Add a separate explicitly named live smoke command if useful.

Use a local fixture modeled on DBMS_proj rather than cloning GitHub in a unit test. A real end-to-end clone/embedding test is optional and must be opt-in because it consumes provider quota and depends on network/repository availability.

Run the appropriate offline pytest suite, type checks, and backend/frontend build checks. Report live Gemini verification separately from offline results. Include the changed files, policy counts, retry classification, and remaining provider limitations in the final handoff.
