# RepoTalk: complete the embedding quota and retry repair

Work in D:\RepoTalk. Inspect current source before editing. Implement this repair with focused changes, preserving existing ingestion safety and unrelated frontend work. Do not migrate frameworks, change embedding models, rotate keys, enable billing, or introduce a new queue/database service as a shortcut.

## Correct the diagnosis first

The reported import had 96 eligible chunks, with 90 embeddings computed across four batches before the final six failed. Treat these counts and timings as reported until verified against sanitized logs. This is not proof that 94% of a usable index was committed.

Removing the generic phrase "check your plan and billing" from the permanent-quota classifier is correct, but does not establish that every remaining 429 is a temporary burst error. Do not claim a universal 15 RPM limit, a particular undocumented burst algorithm, or guaranteed recovery after 2/4/8 seconds. Verify the selected embedding model's actual project limits in AI Studio when evidence is available. Character counts are estimates, not measured tokens. Five application batches do not by themselves prove RPM exhaustion; prior traffic, other consumers, token usage, and actual SDK request behavior matter.

Official reference: https://ai.google.dev/gemini-api/docs/rate-limits. Limits vary by model/project tier and apply per project, not per API key. A successful generation request does not establish sufficient embedding quota for a repository import.

## Phase 1: make retry behavior correct and bounded

Inspect backend/services/gemini_service.py, backend/config.py, backend/services/vector_store.py, ingestion/job endpoints, and backend/tests/test_ingestion_quota.py.

1. Replace message-first classification with a small structured error adapter for the installed google-genai SDK. Inspect the actual exception shape and prefer HTTP status plus structured google.rpc.QuotaFailure and google.rpc.RetryInfo details. Capture quota metric, quota ID, model and retry delay when supplied. Retain conservative string fallbacks only for incomplete responses. Do not log API keys, source chunks, or complete unredacted exception payloads.

2. Distinguish confirmed short-window throttling, confirmed daily quota, explicit billing/access/configuration failures, and unknown-cause 429. Unknown 429 may receive bounded retries, but must remain unknown in the final diagnosis. A daily limit is not permanent forever; it is unsuitable for a short automatic retry loop. Where evidence conflicts, a confirmed nonrecoverable constraint wins over a retry hint. Authentication and invalid request errors must not loop.

3. Parse Retry-After as seconds or HTTP date and structured RetryInfo durations, including fractional seconds. Validate malformed, negative, and non-finite values. Treat provider delay as a minimum: delay = max(local_backoff_with_jitter, provider_delay). The current min(max(backoff, retry_after), EMBEDDING_BACKOFF_MAX_S) is wrong because it truncates provider instructions; the configured cap should cap locally generated backoff only.

4. Add a configurable total automatic-retry deadline, using a monotonic clock and explicit attempt limits. Suggested starting policy: base 2 seconds, local backoff cap 60 seconds, up to 6 retries, total automatic-retry budget 180 seconds, all configurable and validated. These are application policies, not Gemini quotas. Include request execution time in the deadline. If the provider delay exceeds remaining time, stop or defer with an honest retry time; never shorten the delay to squeeze in another request. Align job and HTTP timeouts with this behavior; do not merely leave a long-running request behind a shorter proxy timeout.

5. Route retryable timeouts and selected transport/5xx failures through the same bounded scheduler. The present timeout branch immediately starts another attempt without backoff. Inspect SDK retry defaults to avoid compounded SDK/application retries and account for every real attempt. Cancellation must interrupt waiting and propagate without retrying.

6. Retry only the failed batch within an active ingestion attempt. Preserve successful batch results and their original chunk order. Keep strict count, dimensions, finite-number, and ordering checks before committing vectors. Verify the installed SDK/model multi-input contract; do not assume one Python call equals one quota-counted request or switch to one request per chunk without evidence.

## Phase 2: prevent predictable throttling

7. Retain dual item/character batching, but validate all limits and handle a single oversized chunk explicitly upstream, preserving path/line metadata. Do not silently truncate code. Keep 25 items and 30,000 characters as configurable payload defaults, not as proof of quota compliance. Use a conservative token estimate with a documented safety margin unless accurate token accounting is available without an excessive extra request cost.

8. Replace reliance on a fixed 0.5-second sleep with an async scheduler that enforces configured request and estimated-token budgets over time, and observes provider cooldowns. Use actual configured project/model quota values; when unknown, expose that limitation and use a documented conservative fallback. Requests, retries and interactive query embeddings using the same quota scope must participate. Do not count unrelated text-generation model budgets as identical without evidence.

9. A concurrency semaphore is not a rate limiter. The existing semaphore is process-local and single-batch query embeddings bypass it. Share pacing/cooldowns across calls in one process, scoped to the known project/model budget. Do not assume two different keys mean two different projects, or attempt to decode project identity from a key. Use explicit quota-scope configuration where necessary. Document single-worker limitations; implement shared coordination only if the actual deployment uses multiple workers/instances. Avoid introducing Redis merely for this repair. Do not hold a global lock during backoff in a way that blocks unrelated projects.

## Phase 3: retain useful work without exposing a partial index

10. Check the current storage path: add_chunks currently requests all missing embeddings before committing them. Thus successful earlier batches can be lost when a later batch finally fails. Add bounded, expiring staging checkpoints if cross-attempt resume is needed, using existing storage. Persist only validated batch results, separate from the active searchable index. Key reuse by owner/access scope, repository snapshot/content hash, chunking/input format version, model, task type and embedding dimension. Deduplicate safely across repeated retries; never mix incompatible vectors or reuse another user's private content.

11. Atomically promote only a complete validated index. Preserve an existing valid index if a refresh fails. Keep repository metadata and file-tree semantics consistent with promotion. Clean checkpoints on expiry/deletion and respect ownership. Preserve lockfiles and generated files as metadata where the current policy permits, without embedding them. Use accurate skip reasons for lockfiles versus minified/build files.

12. Make progress truthful: show chunks embedded, current batch, retry attempt and next retry time while waiting. Use wording such as "90 of 96 chunks embedded; waiting before retrying batch 5" only when those counters are real. Never display Ready or 100% until promotion succeeds. Unknown 429 should say the provider rejected the request due to a rate/quota limit whose exact cause was unavailable. Show confirmed daily-quota guidance only with evidence. Preserve structured error codes and optional retry timing through the API to the UI.

13. Relabel the existing generation-only connection test accurately. If adding an embedding capability check, make it an explicit user-triggered minimal request for the configured embedding model, explain that it consumes quota, and do not run it automatically before every import. Passing this check means that one request succeeded at that time, not that a full import is guaranteed.

## Required offline acceptance tests

Use fake clocks, mocked sleeps and realistic structured SDK errors; never spend live quota in unit tests.

- The generic billing phrase in a 429 does not independently trigger permanent-quota classification.
- Structured daily exhaustion does not repeatedly retry; an ambiguous 429 remains ambiguous after its bounded retries.
- Provider delay 45 seconds is respected even when local backoff cap is 16 seconds. A delay beyond the remaining deadline causes no early request.
- RetryInfo fractional durations, HTTP-date Retry-After, malformed hints and absent details are handled safely.
- Batches 1–4 succeed, batch 5 fails once and succeeds: only batch 5 is retried, with 96 correctly ordered vectors committed once.
- Persistent failure, invalid output or cancellation never promotes a partial index, and an existing valid index survives a failed refresh.
- Timeouts back off; attempts and total elapsed time stay bounded, including SDK retries.
- Concurrent ingestion and query embedding obey the shared configured budget; independent scopes are not needlessly blocked.
- Oversized chunks and invalid configuration cannot bypass limits.
- If checkpoints are implemented, resume skips completed compatible chunks, invalidates changed inputs/model/dimensions, enforces ownership, and expires abandoned data.
- UI progress and error messages distinguish waiting, failed, canceled and ready states without exposing provider secrets.

Deliver focused changes, tests and a brief report separating source-confirmed defects, mocked validation and live validation. Do not state that DBMS_proj now succeeds unless an actual authorized end-to-end run completed and its committed index was checked. Do not claim that software can eliminate genuine provider quota exhaustion. Document any deployment limitation or remaining unverified assumption.
