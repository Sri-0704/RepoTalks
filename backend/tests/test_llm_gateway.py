"""Unit tests for LLMGateway (provider routing, failover, cooldowns, and invariants).

Tests:
1. Provider preference resolution (auto, groq, gemini).
2. Routing plan generation with single or dual keys.
3. Pre-first-token failover: Groq rate-limit/capacity failure triggers Gemini fallback.
4. Cooldown tracker: SHA-256 prefix hashing, auto-skipping cooled-down provider.
5. Multimodal guard: requests with image_bytes route to Gemini under auto or raise LLMCapabilityError under groq.
6. Streaming first-token establishment phase and metadata frame.
7. Streaming mid-stream invariant: no failover after first token yielded.
8. Authentication errors when required provider keys are missing.
"""

import asyncio
import time
from typing import AsyncGenerator, Dict, Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.groq_service import (
    GroqAuthenticationError,
    GroqCapacityError,
    GroqEmptyResponseError,
    GroqRateLimitError,
    GroqServiceError,
)
from backend.services.gemini_service import (
    GeminiQuotaExhaustedError,
    GeminiRateLimitError,
    GeminiServiceError,
)
from backend.services.llm_gateway import (
    CooldownTracker,
    GenerationCredentials,
    GenerationRequest,
    LLMAuthenticationError,
    LLMCapabilityError,
    LLMGateway,
    LLMRateLimitError,
    LLMServiceError,
)


@pytest.fixture
def clean_gateway():
    """Create a gateway instance with a fresh cooldown tracker."""
    gateway = LLMGateway()
    gateway.cooldown_tracker = CooldownTracker()
    return gateway


def test_resolve_effective_preference_and_keys(clean_gateway):
    """Test resolution of provider preferences and request-scoped keys."""
    creds = GenerationCredentials(
        gemini_api_key="gem_123",
        groq_api_key="gsk_123",
        provider_preference="auto",
    )
    pref = clean_gateway.resolve_effective_preference(GenerationRequest(prompt="test"), creds)
    assert pref == "auto"

    gem_key, groq_key = clean_gateway.resolve_keys(creds)
    assert gem_key == "gem_123"
    assert groq_key == "gsk_123"

    plan = clean_gateway._determine_plan(GenerationRequest(prompt="test"), pref, gem_key, groq_key)
    assert len(plan) == 2
    assert plan[0][0] == "groq"
    assert plan[1][0] == "gemini"


def test_determine_plan_groq_only(clean_gateway):
    """Test plan when user explicitly chooses Groq or only has Groq key."""
    plan = clean_gateway._determine_plan(GenerationRequest(prompt="test"), "groq", None, "gsk_123")
    assert len(plan) == 1
    assert plan[0][0] == "groq"


def test_determine_plan_gemini_only(clean_gateway):
    """Test plan when user explicitly chooses Gemini or only has Gemini key."""
    plan = clean_gateway._determine_plan(GenerationRequest(prompt="test"), "gemini", "gem_123", None)
    assert len(plan) == 1
    assert plan[0][0] == "gemini"


def test_missing_credentials_raises_auth_error(clean_gateway):
    """Verify that requests without any valid keys immediately raise LLMAuthenticationError."""
    req = GenerationRequest(prompt="test")
    with pytest.raises(LLMAuthenticationError) as exc_info:
        clean_gateway._determine_plan(req, "auto", None, None)
    assert "No LLM API keys configured" in str(exc_info.value)


def test_multimodal_guard(clean_gateway):
    """Verify that multimodal image requests strictly route to Gemini or error cleanly."""
    req = GenerationRequest(
        prompt="Describe architecture diagram",
        image_bytes=b"\x89PNG\r\n\x1a\nfakeimagebytes",
        mime_type="image/png",
    )
    # Under auto, plan routes to Gemini
    plan = clean_gateway._determine_plan(req, "auto", "gem_key", "groq_key")
    assert len(plan) == 1
    assert plan[0][0] == "gemini"

    # Under groq preference, raises LLMCapabilityError
    with pytest.raises(LLMCapabilityError) as exc_info:
        clean_gateway._determine_plan(req, "groq", "gem_key", "groq_key")
    assert "Groq does not support multimodal image analysis" in str(exc_info.value)


def test_cooldown_tracker_sha256_prefix():
    """Verify cooldown tracker stores SHA-256 digests and never raw API keys."""
    tracker = CooldownTracker()
    raw_key = "gsk_super_secret_production_key_456"

    tracker.set_cooldown("groq", raw_key, duration_seconds=10.0)

    # Cooldown should be active
    assert tracker.is_cooled_down("groq", raw_key) is True
    # Verify no raw key exists in the internal dict
    for k in tracker._cooldowns.keys():
        assert raw_key not in k
        assert "groq:" in k

    # Different key is not cooling down
    assert tracker.is_cooled_down("groq", "gsk_different_key") is False


def test_cooldown_skips_groq_in_auto_plan(clean_gateway):
    """Verify that a cooled down Groq key results in Gemini being primary in auto mode."""
    groq_key = "gsk_cooled_down"
    gem_key = "gem_available"

    clean_gateway.cooldown_tracker.set_cooldown("groq", groq_key, 60.0)

    plan = clean_gateway._determine_plan(GenerationRequest(prompt="test"), "auto", gem_key, groq_key)
    # Gemini must be promoted to primary
    assert plan[0][0] == "gemini"


@pytest.mark.asyncio
async def test_generate_text_primary_success(clean_gateway):
    """Verify successful generation through primary Groq provider."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="ping")

    with patch("backend.services.llm_gateway.groq_service.generate_text", new_callable=AsyncMock) as mock_groq:
        mock_groq.return_value = "Groq response"

        res = await clean_gateway.generate_text(req, creds)
        assert res.text == "Groq response"
        assert res.provider == "groq"
        assert res.fallback_used is False


@pytest.mark.asyncio
async def test_generate_text_failover_to_gemini(clean_gateway):
    """Verify automatic failover to Gemini when Groq returns 429 rate limit."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="summarize architecture")

    with (
        patch("backend.services.llm_gateway.groq_service.generate_text", new_callable=AsyncMock) as mock_groq,
        patch("backend.services.llm_gateway.gemini_service.generate_text", new_callable=AsyncMock) as mock_gemini,
    ):
        mock_groq.side_effect = GroqRateLimitError("Rate limit reached", status_code=429, retry_after=12.0)
        mock_gemini.return_value = "Gemini fallback response"

        res = await clean_gateway.generate_text(req, creds)
        assert res.text == "Gemini fallback response"
        assert res.provider == "gemini"
        assert res.fallback_used is True

        # Check that cooldown was recorded
        assert clean_gateway.cooldown_tracker.is_cooled_down("groq", "gsk_123") is True


@pytest.mark.asyncio
async def test_streaming_pre_token_failover(clean_gateway):
    """Verify streaming fails over before the first token is emitted and yields meta event."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="stream trace")

    async def failing_groq_stream(*args, **kwargs):
        raise GroqCapacityError("Groq overloaded", status_code=503)
        yield "never"

    async def successful_gemini_stream(*args, **kwargs):
        yield "Chunk 1 "
        yield "Chunk 2"

    with (
        patch("backend.services.llm_gateway.groq_service.generate_stream", side_effect=failing_groq_stream),
        patch("backend.services.llm_gateway.gemini_service.generate_stream", side_effect=successful_gemini_stream),
    ):
        frames = []
        async for frame in clean_gateway.generate_stream(req, creds):
            frames.append(frame)

        # First frame must be metadata showing failover to gemini
        assert frames[0]["type"] == "meta"
        assert frames[0]["provider"] == "gemini"
        assert frames[0]["fallback_used"] is True

        # Subsequent frames are text chunks
        assert frames[1] == {"text": "Chunk 1 "}
        assert frames[2] == {"text": "Chunk 2"}


@pytest.mark.asyncio
async def test_streaming_midstream_never_fails_over(clean_gateway):
    """Verify that once the first token is emitted, an error yields an error frame and never fails over."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="stream critical flow")

    async def midstream_broken_groq(*args, **kwargs):
        yield "First token"
        raise GroqServiceError("Midstream network drop", status_code=500)

    with (
        patch("backend.services.llm_gateway.groq_service.generate_stream", side_effect=midstream_broken_groq),
        patch("backend.services.llm_gateway.gemini_service.generate_stream") as mock_gemini,
    ):
        frames = []
        async for frame in clean_gateway.generate_stream(req, creds):
            frames.append(frame)

        # Meta was emitted for groq
        assert frames[0] == {"type": "meta", "provider": "groq", "model": "openai/gpt-oss-120b", "fallback_used": False}
        assert frames[1] == {"text": "First token"}
        # Mid-stream error frame emitted, and Gemini was NEVER called
        assert "error" in frames[2]
        assert mock_gemini.call_count == 0


@pytest.mark.asyncio
async def test_streaming_first_content_timeout_with_keepalives_falls_back_in_auto(clean_gateway):
    """Verify that incoming non-content/keep-alive frames do not reset the deadline and trigger fallback in auto mode."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="stream trace", first_content_timeout=0.1)

    async def keepalive_stalling_groq(*args, **kwargs):
        while True:
            await asyncio.sleep(0.03)
            yield ""  # Empty non-content token

    async def successful_gemini_stream(*args, **kwargs):
        yield "Gemini content"

    with (
        patch("backend.services.llm_gateway.groq_service.generate_stream", side_effect=keepalive_stalling_groq),
        patch("backend.services.llm_gateway.gemini_service.generate_stream", side_effect=successful_gemini_stream),
    ):
        frames = []
        async for frame in clean_gateway.generate_stream(req, creds):
            frames.append(frame)

        assert frames[0]["type"] == "meta"
        assert frames[0]["provider"] == "gemini"
        assert frames[0]["fallback_used"] is True
        assert frames[1] == {"text": "Gemini content"}


@pytest.mark.asyncio
async def test_streaming_groq_empty_response_falls_back_in_auto(clean_gateway):
    """Verify GroqEmptyResponseError triggers fallback to Gemini in auto mode."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="stream trace")

    async def empty_groq_stream(*args, **kwargs):
        raise GroqEmptyResponseError("Groq stream completed without final answer content.")
        yield "never"

    async def successful_gemini_stream(*args, **kwargs):
        yield "Gemini answer"

    with (
        patch("backend.services.llm_gateway.groq_service.generate_stream", side_effect=empty_groq_stream),
        patch("backend.services.llm_gateway.gemini_service.generate_stream", side_effect=successful_gemini_stream),
    ):
        frames = []
        async for frame in clean_gateway.generate_stream(req, creds):
            frames.append(frame)

        assert frames[0]["type"] == "meta"
        assert frames[0]["provider"] == "gemini"
        assert frames[0]["fallback_used"] is True
        assert frames[1] == {"text": "Gemini answer"}


@pytest.mark.asyncio
async def test_streaming_explicit_groq_no_fallback_on_timeout(clean_gateway):
    """Verify explicit 'groq' preference yields an error on timeout without falling back to Gemini."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="groq")
    req = GenerationRequest(prompt="stream trace", first_content_timeout=0.05)

    async def stalling_groq(*args, **kwargs):
        await asyncio.sleep(0.5)
        yield "too late"

    with (
        patch("backend.services.llm_gateway.groq_service.generate_stream", side_effect=stalling_groq),
        patch("backend.services.llm_gateway.gemini_service.generate_stream") as mock_gemini,
    ):
        frames = []
        async for frame in clean_gateway.generate_stream(req, creds):
            frames.append(frame)

        assert len(frames) == 1
        assert "error" in frames[0]
        assert "timed out waiting for answer content" in frames[0]["error"]
        assert mock_gemini.call_count == 0


@pytest.mark.asyncio
async def test_streaming_auth_failure_no_fallback(clean_gateway):
    """Verify that authentication error yields an error frame and never falls back."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="stream trace")

    async def auth_failing_groq(*args, **kwargs):
        raise GroqAuthenticationError("Invalid API key", status_code=401)
        yield "never"

    with (
        patch("backend.services.llm_gateway.groq_service.generate_stream", side_effect=auth_failing_groq),
        patch("backend.services.llm_gateway.gemini_service.generate_stream") as mock_gemini,
    ):
        frames = []
        async for frame in clean_gateway.generate_stream(req, creds):
            frames.append(frame)

        assert len(frames) == 1
        assert "error" in frames[0]
        assert "Authentication failed for provider 'groq'" in frames[0]["error"]
        assert mock_gemini.call_count == 0


@pytest.mark.asyncio
async def test_streaming_cancellation_during_first_content_wait(clean_gateway):
    """Verify that client abort raises CancelledError promptly and does not trigger fallback."""
    creds = GenerationCredentials(gemini_api_key="gem_123", groq_api_key="gsk_123", provider_preference="auto")
    req = GenerationRequest(prompt="stream trace", first_content_timeout=5.0)

    groq_stream_started = asyncio.Event()

    async def stalling_groq(*args, **kwargs):
        groq_stream_started.set()
        await asyncio.sleep(10.0)
        yield "too late"

    with (
        patch("backend.services.llm_gateway.groq_service.generate_stream", side_effect=stalling_groq),
        patch("backend.services.llm_gateway.gemini_service.generate_stream") as mock_gemini,
    ):
        async def run_stream():
            async for _ in clean_gateway.generate_stream(req, creds):
                pass

        task = asyncio.create_task(run_stream())
        await groq_stream_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert mock_gemini.call_count == 0

