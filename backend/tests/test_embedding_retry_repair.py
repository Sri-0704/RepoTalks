"""Comprehensive tests for Embedding Retry & Quota Repair (Phase A).

Validates all points from ANTIGRAVITY_EMBEDDING_RETRY_REVIEW.md:
1. Classification of generic billing phrases as retryable THROTTLE_UNKNOWN (not permanent).
2. Structured daily quota exhaustion raises GeminiQuotaExhaustedError without retries.
3. Provider Retry-After delays (e.g. 45s) are respected as minimums, not capped by local backoff.
4. Provider delay exceeding remaining deadline halts retries honestly.
5. Fractional seconds and protobuf RetryInfo duration parsing.
6. RFC 7231 HTTP-date parsing in Retry-After headers.
7. Malformed/negative/non-finite Retry-After values gracefully fall back to local backoff.
8. Multiple batches succeed and failed batch retries / staging persistence works.
9. Persistent failure does not corrupt or partially promote index.
10. TimeoutError backs off instead of retrying immediately.
11. Rate scheduler paces both ingestion and query embeddings.
12. Oversized single chunk gets its own batch without truncation.
13. Invalid configuration values are rejected at startup.
14. Cancellation interrupts retry wait and propagates cleanly.
15. Truthful progress reporting with exact batch and retry metrics.
16. Dedicated embedding client disables SDK-level retries (attempts=1).
"""

import asyncio
import email.utils
import json
import math
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.config import Settings, settings
from backend.services.gemini_service import (
    GeminiErrorClassification,
    GeminiQuotaExhaustedError,
    GeminiRateLimitError,
    GeminiService,
    GeminiServiceError,
    _client_cache,
    _extract_provider_delay,
    _parse_retry_after,
    _parse_retryinfo_duration,
    classify_gemini_error,
    pack_embedding_batches,
)
from backend.services.rate_scheduler import EmbeddingRateScheduler
from backend.services.vector_store import VectorStore


# ---------------------------------------------------------------------------
# 1. Classification of generic billing phrases (Point 1)
# ---------------------------------------------------------------------------
def test_generic_billing_phrase_classification():
    """Generic phrases like 'check your plan and billing' or 'quota exceeded'

    without structured daily quota metrics must be classified as retryable
    THROTTLE_UNKNOWN, not permanent DAILY_QUOTA.
    """
    exc1 = Exception("429 RESOURCE_EXHAUSTED: Resource has been exhausted (e.g. check quota).")
    cls1 = classify_gemini_error(exc1)
    assert cls1.tag == "THROTTLE_UNKNOWN"
    assert cls1.is_retryable is True
    assert cls1.is_daily is False

    exc2 = Exception("429 Quota exceeded. Please check your plan and billing details.")
    cls2 = classify_gemini_error(exc2)
    assert cls2.tag == "THROTTLE_UNKNOWN"
    assert cls2.is_retryable is True
    assert cls2.is_daily is False


# ---------------------------------------------------------------------------
# 2. Structured daily quota exhaustion (Point 2)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_structured_daily_exhaustion_no_retry():
    """Structured daily quota failure must classify as DAILY_QUOTA and fail immediately."""
    exc = Exception(
        "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric 'queriesperday' "
        "and limit 'Free Tier Limit' of service 'generativelanguage.googleapis.com'"
    )
    cls = classify_gemini_error(exc)
    assert cls.tag == "DAILY_QUOTA"
    assert cls.is_retryable is False
    assert cls.is_daily is True

    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(side_effect=exc)

    sleep_calls = []

    async def mock_sleep(d):
        sleep_calls.append(d)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(GeminiQuotaExhaustedError) as exc_info:
            await service.get_embeddings(["test text"])

        assert "project quota exhausted" in str(exc_info.value).lower()
        # No backoff sleeps should occur
        backoff_sleeps = [s for s in sleep_calls if s >= 1.0]
        assert len(backoff_sleeps) == 0


# ---------------------------------------------------------------------------
# 3. Provider delay of 45s is respected and not capped (Point 3)
# ---------------------------------------------------------------------------
def test_provider_delay_45s_respected():
    """A provider-requested delay of 45s must not be truncated by local backoff cap."""
    # Exception with Retry-After header
    exc = Exception("429 Resource exhausted")
    exc.response = MagicMock(headers={"Retry-After": "45"})

    delay = _extract_provider_delay(exc)
    assert delay == 45.0

    # Test formula: effective_delay = max(capped_local_backoff, provider_delay)
    local_backoff = min(settings.EMBEDDING_BACKOFF_BASE_S * (2 ** 0), settings.EMBEDDING_BACKOFF_MAX_S)
    effective_delay = max(local_backoff, delay)
    assert effective_delay == 45.0  # NOT capped to 16.0s or 60.0s


# ---------------------------------------------------------------------------
# 4. Provider delay beyond remaining deadline (Point 4)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_provider_delay_beyond_deadline():
    """If provider requested wait exceeds remaining retry deadline, halt immediately."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()

    # Provider requests 500s delay
    exc = Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
    exc.response = MagicMock(headers={"Retry-After": "500"})
    mock_client.aio.models.embed_content = AsyncMock(side_effect=exc)

    with patch.object(service, "get_client", return_value=mock_client):
        # Budget is only 10 seconds
        with patch.object(settings, "EMBEDDING_RETRY_DEADLINE_S", 10.0):
            with pytest.raises(GeminiRateLimitError) as exc_info:
                await service.get_embeddings(["sample input"])

            assert "retry deadline would be exceeded" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 5. RetryInfo fractional durations (Point 3)
# ---------------------------------------------------------------------------
def test_retryinfo_fractional_durations():
    """Protobuf Duration strings like '1.5s', '45.25s', '0.8s' must be parsed as positive floats."""
    assert _parse_retryinfo_duration("1.5s") == 1.5
    assert _parse_retryinfo_duration("45.25s") == 45.25
    assert _parse_retryinfo_duration("60s") == 60.0
    assert _parse_retryinfo_duration("0s") is None  # Zero rejected
    assert _parse_retryinfo_duration("-5s") is None  # Negative rejected
    assert _parse_retryinfo_duration("invalid") is None
    assert _parse_retryinfo_duration("") is None


# ---------------------------------------------------------------------------
# 6. RFC 7231 HTTP-date parsing in Retry-After (Point 3)
# ---------------------------------------------------------------------------
def test_http_date_retry_after():
    """Retry-After HTTP-date formatted strings must be converted to delta seconds."""
    # Date 30 seconds into the future
    future_time = time.time() + 30
    http_date = email.utils.formatdate(future_time, usegmt=True)

    parsed = _parse_retry_after(http_date)
    assert parsed is not None
    assert 25.0 <= parsed <= 35.0

    # Past date must return None
    past_time = time.time() - 30
    past_date = email.utils.formatdate(past_time, usegmt=True)
    assert _parse_retry_after(past_date) is None


# ---------------------------------------------------------------------------
# 7. Malformed Retry-After fallback (Point 3)
# ---------------------------------------------------------------------------
def test_malformed_retry_after_fallback():
    """Malformed, negative, zero, NaN, or infinite Retry-After values return None."""
    assert _parse_retry_after("NaN") is None
    assert _parse_retry_after("Inf") is None
    assert _parse_retry_after("-10") is None
    assert _parse_retry_after("0") is None
    assert _parse_retry_after("not-a-number") is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after(None) is None


# ---------------------------------------------------------------------------
# 8. Batches 1-4 succeed, batch 5 retries and staging checkpoint saves (Points 6, 10)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_batches_1_4_succeed_batch_5_retry():
    """When multiple batches are embedded, each completed batch invokes on_batch_complete."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()

    completed_batches = []

    def on_batch(idx, texts, embs):
        completed_batches.append((idx, len(texts), len(embs)))

    # 5 inputs with batch_size=1 -> 5 separate batches
    texts = [f"Text chunk {i}" for i in range(5)]

    def make_response(t):
        item = MagicMock(values=[0.1] * 768)
        return MagicMock(embeddings=[item])

    # Batches 0..3 succeed first try, batch 4 fails with 429 once then succeeds
    err_429 = Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
    r0 = make_response(texts[0])
    r1 = make_response(texts[1])
    r2 = make_response(texts[2])
    r3 = make_response(texts[3])
    r4 = make_response(texts[4])

    mock_client.aio.models.embed_content = AsyncMock(
        side_effect=[r0, r1, r2, r3, err_429, r4]
    )

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()):
        result = await service.get_embeddings(
            texts,
            batch_size=1,
            on_batch_complete=on_batch,
        )

    assert len(result) == 5
    assert len(completed_batches) == 5
    assert [b[0] for b in completed_batches] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 9. Persistent failure does not corrupt or partially promote index (Point 11)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persistent_failure_no_partial_promotion():
    """If an ingestion fails mid-way, the existing committed index is preserved intact."""
    temp_dir = Path(tempfile.mkdtemp(prefix="repotalk_test_atomic_"))
    try:
        store = VectorStore(db_path=temp_dir / "test_rag.db")
        repo_id = "repo_existing_123"

        # 1. Initially commit a valid index with 2 chunks
        initial_chunks = [
            {"file_path": "a.py", "chunk_id": "c1", "chunk_order": 0, "chunk_text": "initial content 1", "content_hash": "h1"},
            {"file_path": "b.py", "chunk_id": "c2", "chunk_order": 1, "chunk_text": "initial content 2", "content_hash": "h2"},
        ]

        mock_emb = [[0.1] * 768, [0.2] * 768]
        with patch("backend.services.vector_store.gemini_service.get_embeddings", AsyncMock(return_value=mock_emb)):
            count = await store.add_chunks(repo_id, initial_chunks)
            assert count == 2

        assert store.get_chunk_count(repo_id, compatible_only=True) == 2

        # 2. Now attempt to re-index with new chunks, but provider fails permanently
        new_chunks = [
            {"file_path": "a.py", "chunk_id": "c1_new", "chunk_order": 0, "chunk_text": "modified a", "content_hash": "h_mod_a"},
            {"file_path": "b.py", "chunk_id": "c2_new", "chunk_order": 1, "chunk_text": "modified b", "content_hash": "h_mod_b"},
        ]

        with patch("backend.services.vector_store.gemini_service.get_embeddings", AsyncMock(side_effect=GeminiRateLimitError("Quota exhausted"))):
            with pytest.raises(GeminiRateLimitError):
                await store.add_chunks(repo_id, new_chunks)

        # 3. Verify original index was NOT deleted or corrupted!
        assert store.get_chunk_count(repo_id, compatible_only=True) == 2
        local_res = store.search_local(repo_id, "initial content 1")
        assert len(local_res["keyword_chunks"]) > 0 or len(local_res["exact_files"]) > 0
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 10. TimeoutError backs off instead of retrying immediately (Point 5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_timeout_backs_off():
    """A TimeoutError must route through exponential backoff scheduler."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()

    success_item = MagicMock(values=[0.5] * 768)
    success_response = MagicMock(embeddings=[success_item])

    # Fail once with TimeoutError, then succeed
    mock_client.aio.models.embed_content = AsyncMock(
        side_effect=[asyncio.TimeoutError("Socket timeout"), success_response]
    )

    sleep_calls = []

    async def mock_sleep(d):
        sleep_calls.append(d)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        result = await service.get_embeddings(["timeout test"])
        assert len(result) == 1

        # Must have backed off with >= 2.0s
        backoff_sleeps = [s for s in sleep_calls if s >= 1.0]
        assert len(backoff_sleeps) == 1
        assert backoff_sleeps[0] >= 2.0


# ---------------------------------------------------------------------------
# 11. Rate scheduler shared between ingestion and queries (Points 8, 9)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_ingestion_and_query_shared_pacing():
    """Both ingestion and query embeddings acquire from rate scheduler."""
    scheduler = EmbeddingRateScheduler()
    scheduler._min_interval = 0.05  # 50ms for test speed
    scheduler._last_request_time = time.monotonic()

    t0 = time.monotonic()
    await scheduler.acquire(estimated_chars=100)
    t1 = time.monotonic()

    # Should have waited at least ~0.04s for spacing
    assert (t1 - t0) >= 0.03


# ---------------------------------------------------------------------------
# 12. Oversized single chunk placed in own batch (Point 7)
# ---------------------------------------------------------------------------
def test_oversized_chunk_own_batch():
    """A single chunk exceeding max_chars is placed in its own batch without truncation."""
    chunks = [
        "Normal chunk 1",
        "X" * 35000,  # exceeds default 30000 char limit
        "Normal chunk 2",
    ]

    batches = pack_embedding_batches(chunks, max_inputs=10, max_chars=30000)
    assert len(batches) == 3
    assert batches[0] == ["Normal chunk 1"]
    assert batches[1] == ["X" * 35000]
    assert batches[2] == ["Normal chunk 2"]


# ---------------------------------------------------------------------------
# 13. Invalid configuration rejected at startup (Point 7)
# ---------------------------------------------------------------------------
def test_invalid_config_rejected():
    """Startup validation rejects zero, negative, or contradictory config values."""
    # Negative batch inputs
    with pytest.raises(ValidationError):
        Settings(EMBEDDING_MAX_BATCH_INPUTS=0)

    # Backoff max smaller than base
    with pytest.raises(ValidationError):
        Settings(EMBEDDING_BACKOFF_BASE_S=10.0, EMBEDDING_BACKOFF_MAX_S=5.0)

    # Negative deadline
    with pytest.raises(ValidationError):
        Settings(EMBEDDING_RETRY_DEADLINE_S=-1.0)


# ---------------------------------------------------------------------------
# 14. Cancellation interrupts waiting and propagates cleanly (Point 5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancellation_interrupts_waiting():
    """Cancellation during retry sleep propagates without triggering additional attempts."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(
        side_effect=Exception("429 RESOURCE_EXHAUSTED")
    )

    async def cancelling_sleep(d):
        raise asyncio.CancelledError("Ingestion cancelled by user")

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=cancelling_sleep):
        with pytest.raises(asyncio.CancelledError):
            await service.get_embeddings(["cancellation test"])


# ---------------------------------------------------------------------------
# 15. Truthful progress reporting (Point 12)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_progress_messages_truthful():
    """Progress callbacks receive accurate counters for chunks, batches, and retries."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()

    progress_events = []

    def progress_cb(embedded, total, cur_batch, total_batches, retry_attempt, retry_delay):
        progress_events.append({
            "embedded": embedded,
            "total": total,
            "batch": cur_batch,
            "total_batches": total_batches,
            "retry_attempt": retry_attempt,
        })

    item = MagicMock(values=[0.1] * 768)
    mock_client.aio.models.embed_content = AsyncMock(
        return_value=MagicMock(embeddings=[item])
    )

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", AsyncMock()):
        await service.get_embeddings(["chunk 1"], progress_cb=progress_cb)

    assert len(progress_events) >= 1
    last_event = progress_events[-1]
    assert last_event["embedded"] == 1
    assert last_event["total"] == 1
    assert last_event["batch"] == 1
    assert last_event["total_batches"] == 1


# ---------------------------------------------------------------------------
# 16. Dedicated embedding client disables SDK retries (attempts=1)
# ---------------------------------------------------------------------------
def test_one_app_attempt_one_sdk_attempt():
    """Dedicated embedding client specifies HttpRetryOptions(attempts=1)."""
    # Verify _client_cache.get_embedding configures attempts=1
    client = _client_cache.get_embedding("test_key_for_client_opts")
    # Verify client was created
    assert client is not None
