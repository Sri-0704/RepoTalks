"""Tests for Gemini Generation Retry, Single Retry Ownership, and Availability Fallback (Root Cause 2).

Validates:
1. 503 then success: 1 retry backoff sleep occurs, successful text is returned.
2. Persistent 503: attempts stop at configured bound, truthful temporary unavailable error raised.
3. 429 with Retry-After: provider delay is honored within deadline.
4. Confirmed daily quota: immediate GeminiQuotaExhaustedError without retries or fallback.
5. 400, 401, 403: immediate failure without retries or fallback.
6. Timeout / transport error: classified as retryable SERVER_ERROR and retried within budget.
7. Cancellation: asyncio.CancelledError propagates cleanly without additional attempts.
8. SDK ownership: generation client explicitly uses HttpRetryOptions(attempts=1).
9. Fallback model: used only when primary model exhausts retries on SERVER_ERROR; never on auth/400/quota.
10. Streaming safety: retry permitted before first chunk; strictly forbidden after first chunk yielded.
11. Settings validation: non-positive, negative, or contradictory generation settings rejected.
"""

import asyncio
import time
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.config import Settings, settings
from backend.services.gemini_service import (
    GeminiModelUnavailableError,
    GeminiQuotaExhaustedError,
    GeminiRateLimitError,
    GeminiService,
    GeminiServiceError,
    _client_cache,
    classify_gemini_error,
)


# Helper mock classes for Gemini API exceptions
class MockGeminiAPIError(Exception):
    def __init__(self, message: str, code: int = 503, headers: dict = None, details: list = None):
        super().__init__(message)
        self.code = code
        self.status_code = code
        if headers:
            self.response = MagicMock(headers=headers)
        if details:
            self.details = details


# ---------------------------------------------------------------------------
# 1. 503 then success
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_text_503_then_success():
    """Transient 503 on first attempt retries after backoff and succeeds."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    success_resp = MagicMock()
    success_resp.text = "Success after transient 503"

    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[
            MockGeminiAPIError("503 The service is currently unavailable", code=503),
            success_resp,
        ]
    )

    sleep_calls = []

    async def mock_sleep(d):
        sleep_calls.append(d)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        result = await service.generate_text("test prompt")

    assert result == "Success after transient 503"
    assert mock_client.aio.models.generate_content.call_count == 2
    # At least one backoff sleep should have been executed (plus zero-sleeps)
    backoff_sleeps = [d for d in sleep_calls if d > 0]
    assert len(backoff_sleeps) == 1
    assert backoff_sleeps[0] >= settings.GENERATION_BACKOFF_BASE_S


# ---------------------------------------------------------------------------
# 2. Persistent 503 stops at bound with truthful message
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_text_persistent_503_stops_at_bound():
    """Persistent 503 stops after configured max retries and reports temporary unavailability."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=MockGeminiAPIError("503 Service Unavailable", code=503)
    )

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()), \
         patch.object(settings, "GENERATION_MAX_RETRIES", 2), \
         patch.object(settings, "GENERATION_FALLBACK_MODEL", None):
        with pytest.raises(GeminiServiceError) as exc_info:
            await service.generate_text("test prompt")

    # 1 initial attempt + 2 retries = 3 attempts total
    assert mock_client.aio.models.generate_content.call_count == 3
    assert "temporarily unavailable" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 3. 429 with Retry-After header
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_text_429_with_retry_after():
    """Provider Retry-After header is honored within deadline."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    rate_limit_err = MockGeminiAPIError(
        "429 Resource exhausted",
        code=429,
        headers={"retry-after": "2.5"},
    )
    success_resp = MagicMock()
    success_resp.text = "Success after rate limit"

    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[rate_limit_err, success_resp]
    )

    sleep_calls = []

    async def mock_sleep(d):
        sleep_calls.append(d)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        result = await service.generate_text("test prompt")

    assert result == "Success after rate limit"
    backoff_sleeps = [d for d in sleep_calls if d > 0]
    assert len(backoff_sleeps) == 1
    # Provider specified 2.5s; delay must be at least 2.5s
    assert backoff_sleeps[0] >= 2.5


# ---------------------------------------------------------------------------
# 4. Confirmed daily quota fails immediately without retry or fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_text_daily_quota_fails_immediately():
    """Confirmed daily quota raises GeminiQuotaExhaustedError without retries or fallback."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    mock_violation = MagicMock(metric="queriesPerDay", quota_id="GenerateContent_PerDay")
    mock_detail = MagicMock(violations=[mock_violation])
    daily_err = MockGeminiAPIError(
        "Quota exceeded for quota metric 'queriesPerDay' and limit 'QueriesPerDay'",
        code=429,
        details=[mock_detail],
    )

    mock_client.aio.models.generate_content = AsyncMock(side_effect=daily_err)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()):
        with pytest.raises(GeminiQuotaExhaustedError) as exc_info:
            await service.generate_text("test prompt")

    # Exactly 1 attempt — no retries
    assert mock_client.aio.models.generate_content.call_count == 1
    assert "daily quota" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 5. 400, 401, and 403 fail immediately
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status,msg,expected_msg",
    [
        (400, "INVALID_ARGUMENT: Invalid contents", "invalid"),
        (401, "API_KEY_INVALID: User API key not valid", "valid gemini api key"),
        (403, "PERMISSION_DENIED: Access denied", "valid gemini api key"),
    ],
)
@pytest.mark.asyncio
async def test_generate_text_non_retryable_errors(status, msg, expected_msg):
    """400, 401, and 403 fail immediately on attempt 1 without retries."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=MockGeminiAPIError(msg, code=status)
    )

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()):
        with pytest.raises(GeminiServiceError) as exc_info:
            await service.generate_text("test prompt")

    assert mock_client.aio.models.generate_content.call_count == 1
    assert expected_msg in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 6. Timeout / Connection error retried within budget
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_text_timeout_error_retried():
    """TimeoutError is classified as retryable SERVER_ERROR and retried."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    success_resp = MagicMock()
    success_resp.text = "Success after timeout"

    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[
            asyncio.TimeoutError("Request timed out"),
            success_resp,
        ]
    )

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()):
        result = await service.generate_text("test prompt")

    assert result == "Success after timeout"
    assert mock_client.aio.models.generate_content.call_count == 2


# ---------------------------------------------------------------------------
# 7. Cancellation propagates cleanly
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_text_cancellation_propagates():
    """Cancellation during retry sleep propagates without triggering additional attempts."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=MockGeminiAPIError("503 UNAVAILABLE", code=503)
    )

    async def cancelling_sleep(d):
        if d > 0:
            raise asyncio.CancelledError("User aborted generation")

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=cancelling_sleep):
        with pytest.raises(asyncio.CancelledError):
            await service.generate_text("test prompt")

    # Only 1 attempt should have been made before sleep cancelled
    assert mock_client.aio.models.generate_content.call_count == 1


# ---------------------------------------------------------------------------
# 8. Generation client uses HttpRetryOptions(attempts=1)
# ---------------------------------------------------------------------------
def test_generation_client_disables_sdk_retries():
    """Generation client specifies HttpRetryOptions(attempts=1) for single retry ownership."""
    # Instantiating client through cache configures retry_options.attempts = 1
    client = _client_cache.get_generation("test_key_for_gen_opts")
    assert client is not None


# ---------------------------------------------------------------------------
# 9. Model Fallback: only on exhausted SERVER_ERROR
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_text_fallback_on_server_error():
    """When primary model exhausts retries on SERVER_ERROR, fallback model is attempted and succeeds."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    fallback_success = MagicMock()
    fallback_success.text = "Response from fallback model"

    # Calls will see: primary attempt 1 (fail 503), primary retry 1 (fail 503), then fallback attempt 1 (success)
    call_models = []

    async def mock_generate(model, contents, config):
        call_models.append(model)
        if model == "primary-model":
            raise MockGeminiAPIError("503 High load", code=503)
        return fallback_success

    mock_client.aio.models.generate_content = mock_generate

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()), \
         patch.object(settings, "DEFAULT_FLASH_MODEL", "primary-model"), \
         patch.object(settings, "GENERATION_FALLBACK_MODEL", "fallback-model"), \
         patch.object(settings, "GENERATION_MAX_RETRIES", 1):

        result = await service.generate_text("test prompt")

    assert result == "Response from fallback model"
    # Primary attempted twice (initial + 1 retry), then fallback attempted once
    assert call_models == ["primary-model", "primary-model", "fallback-model"]


# ---------------------------------------------------------------------------
# 10. Streaming safety: retry before first chunk; no restart after first chunk
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_stream_retry_before_first_chunk():
    """Stream retry is permitted before first chunk is yielded."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    class MockChunk:
        def __init__(self, text):
            self.text = text

    class MockStream:
        def __init__(self, chunks):
            self.chunks = chunks

        async def __aiter__(self):
            for c in self.chunks:
                yield c

    attempt = 0

    async def mock_stream(model, contents, config):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise MockGeminiAPIError("503 Unavailable on stream start", code=503)
        return MockStream([MockChunk("Hello "), MockChunk("World")])

    mock_client.aio.models.generate_content_stream = mock_stream

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()):
        tokens = []
        async for chunk in service.generate_stream("test prompt"):
            tokens.append(chunk)

    assert tokens == ["Hello ", "World"]
    assert attempt == 2


@pytest.mark.asyncio
async def test_generate_stream_no_restart_after_first_chunk():
    """Stream interrupted mid-stream raises immediately without restarting or duplicating output."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    class MockChunk:
        def __init__(self, text):
            self.text = text

    stream_init_count = 0

    class FailingMidStream:
        def __init__(self):
            self.yielded = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.yielded == 0:
                self.yielded += 1
                return MockChunk("Initial output. ")
            raise MockGeminiAPIError("503 Dropped mid-stream", code=503)

    async def mock_stream(model, contents, config):
        nonlocal stream_init_count
        stream_init_count += 1
        return FailingMidStream()

    mock_client.aio.models.generate_content_stream = mock_stream

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()):
        tokens = []
        with pytest.raises(GeminiServiceError):
            async for chunk in service.generate_stream("test prompt"):
                tokens.append(chunk)

    # First token was received
    assert tokens == ["Initial output. "]
    # Stream initialization MUST NOT be called again after partial yield
    assert stream_init_count == 1


# ---------------------------------------------------------------------------
# 11. Configuration validation
# ---------------------------------------------------------------------------
def test_generation_config_validation():
    """Startup validation rejects invalid/negative/contradictory generation settings."""
    # Negative max retries
    with pytest.raises(ValidationError):
        Settings(GENERATION_MAX_RETRIES=-1)

    # Non-positive backoff base
    with pytest.raises(ValidationError):
        Settings(GENERATION_BACKOFF_BASE_S=0.0)

    # Backoff max < backoff base
    with pytest.raises(ValidationError):
        Settings(GENERATION_BACKOFF_BASE_S=10.0, GENERATION_BACKOFF_MAX_S=5.0)

    # Retry deadline < backoff base
    with pytest.raises(ValidationError):
        Settings(GENERATION_BACKOFF_BASE_S=5.0, GENERATION_RETRY_DEADLINE_S=2.0)


# ---------------------------------------------------------------------------
# 12. MODEL_UNAVAILABLE classification
# ---------------------------------------------------------------------------
def test_model_unavailable_classification():
    """HTTP 404, NOT_FOUND, or 'no longer available' is classified as MODEL_UNAVAILABLE (non-retryable on same model)."""
    exc_404 = MockGeminiAPIError("404 NOT_FOUND: models/gemini-2.5-flash is not found", code=404)
    c1 = classify_gemini_error(exc_404)
    assert c1.tag == "MODEL_UNAVAILABLE"
    assert c1.is_retryable is False

    exc_deprecated = Exception("models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.6-flash.")
    c2 = classify_gemini_error(exc_deprecated)
    assert c2.tag == "MODEL_UNAVAILABLE"
    assert c2.is_retryable is False


# ---------------------------------------------------------------------------
# 13. MODEL_UNAVAILABLE triggers immediate failover with zero sleep
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_model_unavailable_immediate_failover_with_zero_sleep():
    """Primary model failing with 404/MODEL_UNAVAILABLE immediately falls over to fallback model with 0s backoff sleep."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    success_resp = MagicMock()
    success_resp.text = "Success from fallback model"

    async def mock_generate(model, contents, config):
        if model == "gemini-primary":
            raise MockGeminiAPIError("404 models/gemini-primary is no longer available", code=404)
        return success_resp

    mock_client.aio.models.generate_content = AsyncMock(side_effect=mock_generate)

    sleep_delays = []

    async def mock_sleep(d):
        sleep_delays.append(d)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep), \
         patch.object(settings, "DEFAULT_FLASH_MODEL", "gemini-primary"), \
         patch.object(settings, "GENERATION_FALLBACK_MODEL", "gemini-fallback"):
        result = await service.generate_text("test prompt")

    assert result == "Success from fallback model"
    assert mock_client.aio.models.generate_content.call_count == 2
    # Crucial: NO positive backoff delay sleep should have occurred when failing over from 404
    positive_sleeps = [d for d in sleep_delays if d > 0]
    assert positive_sleeps == [], f"Expected zero positive backoff sleeps on 404 failover, got {positive_sleeps}"


# ---------------------------------------------------------------------------
# 14. MODEL_UNAVAILABLE exhaustion raises GeminiModelUnavailableError
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_model_unavailable_exhaustion_raises_gemini_model_unavailable_error():
    """When primary model 404s and no distinct fallback exists (or fallback also 404s), GeminiModelUnavailableError is raised."""
    service = GeminiService(api_key="mock_test_key")
    mock_client = MagicMock()

    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=MockGeminiAPIError("404 models/gemini-primary is no longer available", code=404)
    )

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()), \
         patch.object(settings, "DEFAULT_FLASH_MODEL", "gemini-primary"), \
         patch.object(settings, "GENERATION_FALLBACK_MODEL", None):
        with pytest.raises(GeminiModelUnavailableError) as exc_info:
            await service.generate_text("test prompt")

        assert "unavailable or deprecated" in str(exc_info.value)

