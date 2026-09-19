# Workspace Chat Streaming Stall & Empty-Answer Remediation Report

## 1. Executive Summary

This remediation resolves the Workspace Chat streaming stall and empty-answer defect in RepoTalks AI. 

When users asked questions in Workspace Chat with both Groq (`openai/gpt-oss-120b`) and Gemini configured, citations were retrieved and displayed correctly, but the assistant answer text remained completely blank or stalled indefinitely.

The root causes have been eliminated across the backend configuration, Groq service adapter, LLM Gateway, and frontend UI. All 40 unit and integration tests passed, and the Next.js static export build succeeded without errors.

---

## 2. Root Cause Analysis

1. **Groq Reasoning Model Behavior**:
   - Models like `openai/gpt-oss-120b` emit reasoning/thinking tokens (`delta.reasoning` or `delta.reasoning_content`) while leaving `delta.content` empty or whitespace for prolonged periods or the entire stream.
   - The Groq API rejects requests with `reasoning_format: "hidden"` for `openai/gpt-oss-120b` (returns HTTP 400). It instead supports `include_reasoning: false`.
   - When the stream terminated with `[DONE]`, zero `content` tokens had been emitted.
2. **Missing First-Content Deadline in Gateway**:
   - The LLM Gateway enforced an overall read timeout per chunk, but keepalive pings and reasoning deltas reset the read timer indefinitely.
   - If no text tokens arrived, the stream blocked the client until the browser or user aborted.
3. **No Failover in Auto Mode After Stream Handshake**:
   - Because the HTTP connection to Groq was established (200 OK), the gateway considered the stream "started" and never fell back to Gemini, even though zero actual answer text was produced.
4. **Frontend UI Silent Failure**:
   - When the SSE stream closed with zero text, the frontend finalized the message with an empty body.
   - Citations were displayed under an empty bubble, giving no indication of failure or opportunity to retry.

---

## 3. Remediations Implemented

### Backend Configuration & Contracts
- **`backend/config.py`**:
  - Added `LLM_FIRST_CONTENT_TIMEOUT_S: float = 15.0`.
  - Added Pydantic validator ensuring `0 < LLM_FIRST_CONTENT_TIMEOUT_S <= LLM_REQUEST_TIMEOUT_S`.
- **`.env.example`**:
  - Documented `LLM_FIRST_CONTENT_TIMEOUT_S=15.0`.

### Groq Service Adapter (`backend/services/groq_service.py`)
- Added `GroqEmptyResponseError(GroqServiceError)`.
- Added `_supports_include_reasoning` helper:
  - For models containing `gpt-oss`, `deepseek`, or `qwen`, sets `payload["include_reasoning"] = False` without setting unsupported `reasoning_format: "hidden"`.
- **In `generate_text`**:
  - Validates that the final returned text is not empty or whitespace.
  - Never leaks reasoning or `<think>` tags into user answers.
  - Raises `GroqEmptyResponseError` if no final answer text was produced.
- **In `test_connection`**:
  - Catches `GroqEmptyResponseError` and marks the connection test as failed (`success: False`) when the probe produces no text.
- **In `generate_stream`**:
  - Filters out reasoning deltas.
  - Tracks `content_frames_count`, `non_content_frames_count`, `ttft_ms`, and `finish_reason`.
  - Raises `GroqEmptyResponseError` if the stream finishes (`[DONE]`) with zero content frames.

### LLM Gateway (`backend/services/llm_gateway.py`)
- Added `LLMEmptyContentError(LLMServiceError)`.
- Added `first_content_timeout` to `GenerationRequest`.
- In `generate_stream`:
  - Wrapped acquisition of the first content token in `asyncio.wait_for(..., timeout=effective_first_content_timeout)`.
  - **Pre-token failover**: If a timeout, `GroqEmptyResponseError`, or connection failure occurs before any text token is yielded:
    - In `auto` mode: Logs the failure and transparently falls back to Gemini.
    - In explicit `groq` mode: Raises a typed error to the client.
  - **Midstream stability**: Once the first token is yielded, failover is locked out to prevent corrupted, mixed-model responses.
  - **Cancellation handling**: Client disconnections during the first-content wait promptly release the upstream generator without triggering failover.

### Frontend Client & UI (`frontend/lib/api.ts`, `frontend/app/page.tsx`)
- **`frontend/lib/api.ts`**:
  - Updated `streamChat` to track `contentReceived: boolean` and return `{ completed: boolean, contentReceived: boolean }`.
- **`frontend/app/page.tsx`**:
  - Added `userStoppedRef` and `accumulatedRef` for message streaming lifecycle tracking.
  - In `MessageRow`: Always renders citations regardless of whether the message ended in success or error.
  - In `handleSendMessage`:
    - Handles user-aborted streams gracefully ("Response stopped before any text arrived").
    - If a stream finishes with zero content and was not user-aborted, sets a clear, actionable error: *"No response text was received from the model. Please retry or switch providers in Settings."*
    - Scoped `updateAssistant` before `try`/`catch` to ensure assistant state is updated safely under `activeRepo.repo_id`.

---

## 4. Before & After Behavior Matrix

| Scenario | Before Fix | After Fix |
| :--- | :--- | :--- |
| **Groq `openai/gpt-oss-120b` (Auto Mode)** | Stalled indefinitely or closed with empty text; citations displayed above blank bubble. | Sends `include_reasoning: false`. If first token does not arrive within 15s or stream ends empty, automatically fails over to Gemini; complete answer streams smoothly. |
| **Groq (Explicit Provider)** | Stalled or closed with empty text; no clear error or retry action. | Enforces 15s first-token deadline; raises clear `LLMEmptyContentError`; UI displays actionable error message and preserves citations. |
| **Gemini (Direct or Fallback)** | Functioned normally, but was never triggered when Groq stalled in auto mode. | Triggered instantly if Groq produces no text within 15s; answers and citations stream seamlessly. |
| **User Abort Before Text** | Left empty or corrupted message bubble. | Cleanly stops upstream request and displays "Response stopped before any text arrived". |

---

## 5. Verification & Test Results

### 1. Backend Automated Tests
Ran the full offline test suite covering Groq service reasoning handling, LLM Gateway failovers, and server security:
```powershell
.\venv\Scripts\python.exe -m pytest backend/tests/test_groq_service.py backend/tests/test_llm_gateway.py backend/test_server.py -v
```
**Result**:
- `40 passed in 41.86s` (100% pass rate).
- Verified scenarios:
  - `test_groq_reasoning_only_raises_empty_content_error`: PASSED
  - `test_groq_streaming_reasoning_only_then_done_raises_empty_error`: PASSED
  - `test_groq_include_reasoning_payload_contract`: PASSED
  - `test_streaming_first_content_timeout_with_keepalives_falls_back_in_auto`: PASSED
  - `test_streaming_groq_empty_response_falls_back_in_auto`: PASSED
  - `test_streaming_explicit_groq_no_fallback_on_timeout`: PASSED
  - `test_streaming_auth_failure_no_fallback`: PASSED
  - `test_streaming_cancellation_during_first_content_wait`: PASSED
  - All 8 `test_server.py` regression/security tests: PASSED

### 2. Frontend Build & Static Export
Ran the Next.js static export check:
```powershell
Set-Location frontend
npm run build
```
**Result**:
- Icon validation: PASSED.
- TypeScript validation (`tsc --noEmit`): 0 errors.
- Next.js static page generation: `11/11 pages prerendered as static content`.
- Export compatible with unified deployment (`output: 'export'`).

---

## 6. Security & Invariant Adherence

- **No Secret Leakage**: API keys are never logged, persisted in SQLite, or transmitted in client errors.
- **Provider Failure Honesty**: Provider stalls and empty responses are reported honestly without fabricated content or hallucinated answers.
- **Session Isolation**: All session ownership and repository containment checks remain intact.
- **Deterministic Cleanup**: Stalled streams and cancelled tasks immediately release sockets and generators.
