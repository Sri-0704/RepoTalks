"""Offline mocked test suite for repository ingestion quota fix.

Validates:
1. IndexingPolicy case-insensitivity, lockfile matching, and generated asset skipping.
2. Metadata retention in repository summary/tree vs exclusion from semantic chunks.
3. Dual-budget request batch packing (item count + character budget).
4. Embedding cardinality, ordering, and dimension consistency validation.
5. Transient 429 retry with exponential backoff and Retry-After support.
6. Immediate rejection on non-transient errors (invalid key, project/daily quota exhaustion).
7. Cancellation propagation during backoff.
8. Transactional rollback and prevention of partial index promotion on failure.
9. Process-level concurrency semaphore serialization during batch ingestion.

All tests in this file are 100% offline and do not make network calls or spend Gemini quota.
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import settings
from backend.services.gemini_service import (
    GeminiQuotaExhaustedError,
    GeminiRateLimitError,
    GeminiService,
    GeminiServiceError,
    _classify_gemini_error,
    pack_embedding_batches,
)
from backend.services.ingestion_service import IndexingPolicy, IngestionService
from backend.services.vector_store import VectorStore


# ---------------------------------------------------------------------------
# 1. IndexingPolicy matching and case normalization
# ---------------------------------------------------------------------------
def test_indexing_policy_matching_and_case_normalization():
    """Verify lockfiles and minified files are identified case-insensitively."""
    lockfiles = [
        "package-lock.json",
        "PACKAGE-LOCK.JSON",
        "Package-Lock.json",
        "yarn.lock",
        "YARN.LOCK",
        "pnpm-lock.yaml",
        "bun.lockb",
        "poetry.lock",
        "Pipfile.lock",
        "PIPFILE.LOCK",
        "uv.lock",
        "Cargo.lock",
        "CARGO.LOCK",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "path/to/nested/package-lock.json",
    ]
    for lf in lockfiles:
        is_indexable, reason = IndexingPolicy.evaluate_file(lf)
        assert not is_indexable, f"Expected {lf} to be non-indexable"
        assert reason == "generated_dependency_file"

    minified_assets = [
        "bundle.min.js",
        "APP.MIN.CSS",
        "bundle.js.map",
        "app.bundle.js",
        "theme.bundle.css",
        "nested/dir/styles.min.css",
    ]
    for ma in minified_assets:
        is_indexable, reason = IndexingPolicy.evaluate_file(ma)
        assert not is_indexable, f"Expected {ma} to be non-indexable"
        assert reason == "generated_minified_asset"

    regular_files = [
        "package.json",
        "server.js",
        "main.py",
        "app.tsx",
        "README.md",
        "schema.sql",
        "config.yaml",
        ".env.example",
    ]
    for rf in regular_files:
        is_indexable, reason = IndexingPolicy.evaluate_file(rf)
        assert is_indexable, f"Expected {rf} to be indexable"
        assert reason is None


# ---------------------------------------------------------------------------
# 2. Retained metadata versus excluded chunk output
# ---------------------------------------------------------------------------
def test_retained_metadata_versus_excluded_chunk_output():
    """Lockfiles remain visible in tree/files but are excluded from chunks."""
    temp_dir = Path(tempfile.mkdtemp(prefix="repotalk_test_meta_"))
    try:
        # Create a mock repository structure modeled after DBMS_proj
        (temp_dir / "package.json").write_text('{"name": "test-app", "version": "1.0.0"}', encoding="utf-8")
        (temp_dir / "package-lock.json").write_text('{"name": "test-app", "lockfileVersion": 2, "dependencies": {"express": "4.18.2"}}', encoding="utf-8")
        (temp_dir / "server.js").write_text('const express = require("express");\napp.listen(3000);', encoding="utf-8")
        
        static_dir = temp_dir / "static"
        static_dir.mkdir(parents=True, exist_ok=True)
        (static_dir / "bundle.min.js").write_text('function a(){console.log("minified");}', encoding="utf-8")

        svc = IngestionService()
        summary = svc.analyze_repository(temp_dir, "test_repo_1", "DBMS_proj_mock")

        # 1. Total files should contain all 4 text files
        file_paths = {f["path"] for f in summary["files"]}
        assert "package.json" in file_paths
        assert "package-lock.json" in file_paths
        assert "server.js" in file_paths
        assert "static/bundle.min.js" in file_paths

        # 2. Verify metadata markers
        files_by_path = {f["path"]: f for f in summary["files"]}
        assert files_by_path["package-lock.json"]["indexed"] is False
        assert files_by_path["package-lock.json"]["index_skip_reason"] == "generated_dependency_file"
        assert files_by_path["static/bundle.min.js"]["indexed"] is False
        assert files_by_path["static/bundle.min.js"]["index_skip_reason"] == "generated_minified_asset"
        assert files_by_path["server.js"]["indexed"] is True
        assert files_by_path["server.js"]["index_skip_reason"] is None

        # 3. Verify chunks: No chunk should originate from package-lock.json or bundle.min.js
        chunk_files = {c["file_path"] for c in summary["chunks"]}
        assert "package-lock.json" not in chunk_files
        assert "static/bundle.min.js" not in chunk_files
        assert "server.js" in chunk_files
        assert "package.json" in chunk_files

        # 4. Verify skipped stats
        assert summary["skipped_indexing_stats"]["file_count"] == 2
        assert summary["skipped_indexing_stats"]["char_count"] > 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. Request batching by input count and character budget
# ---------------------------------------------------------------------------
def test_request_batching_by_input_and_character_budget():
    """Test dual-budget batch packing respects both item count and character limit."""
    # Test 1: Count budget
    texts = [f"Text chunk #{i}" for i in range(60)]
    batches = pack_embedding_batches(texts, max_inputs=25, max_chars=30000)
    assert len(batches) == 3
    assert len(batches[0]) == 25
    assert len(batches[1]) == 25
    assert len(batches[2]) == 10

    # Test 2: Character budget
    # 4 chunks of 10,000 characters with max_chars=15000 -> each text gets its own batch
    big_texts = ["A" * 10000 for _ in range(4)]
    batches_chars = pack_embedding_batches(big_texts, max_inputs=25, max_chars=15000)
    assert len(batches_chars) == 4
    for b in batches_chars:
        assert len(b) == 1

    # Test 3: Oversized chunk placed in its own batch
    oversized = ["B" * 40000, "small"]
    batches_over = pack_embedding_batches(oversized, max_inputs=25, max_chars=30000)
    assert len(batches_over) == 2
    assert batches_over[0] == ["B" * 40000]
    assert batches_over[1] == ["small"]

    # Test 4: Empty input returns empty
    assert pack_embedding_batches([], max_inputs=25, max_chars=30000) == []


# ---------------------------------------------------------------------------
# 4. Successful embedding cardinality, order, and dimension validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_embedding_cardinality_and_dimension_validation():
    """Ensure response cardinality and vector dimensions are strictly validated."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()
    mock_aio = MagicMock()
    mock_models = MagicMock()
    mock_client.aio = mock_aio
    mock_aio.models = mock_models

    with patch.object(service, "get_client", return_value=mock_client):
        # Case A: Valid response (3 inputs -> 3 vectors of dim 768)
        item1 = MagicMock(values=[0.1] * 768)
        item2 = MagicMock(values=[0.2] * 768)
        item3 = MagicMock(values=[0.3] * 768)
        mock_response = MagicMock(embeddings=[item1, item2, item3])
        mock_models.embed_content = AsyncMock(return_value=mock_response)

        texts = ["Chunk 1", "Chunk 2", "Chunk 3"]
        result = await service.get_embeddings(texts)
        assert len(result) == 3
        assert len(result[0]) == 768
        assert result[0][0] == 0.1
        assert result[1][0] == 0.2
        assert result[2][0] == 0.3

        # Case B: Cardinality mismatch (provider aggregates 3 inputs into 1 vector)
        mock_response_single = MagicMock(embeddings=[item1])
        mock_models.embed_content = AsyncMock(return_value=mock_response_single)
        with pytest.raises(GeminiServiceError) as exc_info:
            await service.get_embeddings(texts)
        assert "cardinality mismatch" in str(exc_info.value).lower()

        # Case C: Inconsistent vector dimensions
        bad_item = MagicMock(values=[0.9] * 512)
        mock_response_bad_dim = MagicMock(embeddings=[item1, item2, bad_item])
        mock_models.embed_content = AsyncMock(return_value=mock_response_bad_dim)
        with pytest.raises(GeminiServiceError) as exc_info:
            await service.get_embeddings(texts)
        assert "inconsistent vector dimensions" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 5. Retry on transient 429 with mocked sleep
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_on_transient_429_with_mocked_sleep():
    """Verify exponential backoff retry on transient 429 rate limit."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()
    mock_aio = MagicMock()
    mock_models = MagicMock()
    mock_client.aio = mock_aio
    mock_aio.models = mock_models

    # Fail twice with 429 RESOURCE_EXHAUSTED, succeed on 3rd attempt
    err_429 = Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded for RPM")
    success_item = MagicMock(values=[0.5] * 768)
    success_response = MagicMock(embeddings=[success_item])

    mock_models.embed_content = AsyncMock(side_effect=[err_429, err_429, success_response])

    sleep_calls = []
    async def mock_sleep(duration):
        sleep_calls.append(duration)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        result = await service.get_embeddings(["Single chunk"])
        assert len(result) == 1
        assert len(result[0]) == 768
        # Should have retried twice (filtering out rate-scheduler pacing intervals < 1s)
        backoff_sleeps = [s for s in sleep_calls if s >= 1.0]
        assert len(backoff_sleeps) == 2
        # Backoff schedule: attempt 0 ~ 2s, attempt 1 ~ 4s
        assert backoff_sleeps[0] >= 2.0
        assert backoff_sleeps[1] >= 4.0


# ---------------------------------------------------------------------------
# 6. No retry on invalid key or permanent quota exhaustion
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_retry_on_invalid_key_or_permanent_quota():
    """Ensure non-transient errors fail immediately without retry."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()
    mock_aio = MagicMock()
    mock_models = MagicMock()
    mock_client.aio = mock_aio
    mock_aio.models = mock_models

    sleep_calls = []
    async def mock_sleep(duration):
        sleep_calls.append(duration)

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=mock_sleep):

        # Subtest A: Invalid API key -> immediate failure, 0 backoff sleeps
        mock_models.embed_content = AsyncMock(side_effect=Exception("API_KEY_INVALID: API key not valid"))
        with pytest.raises(GeminiServiceError) as exc_info:
            await service.get_embeddings(["Test input"])
        assert "Invalid Gemini API key" in str(exc_info.value)
        backoff_sleeps_a = [s for s in sleep_calls if s >= 1.0]
        assert len(backoff_sleeps_a) == 0

        # Subtest B: Permanent daily quota exhaustion -> immediate failure, 0 backoff sleeps
        mock_models.embed_content = AsyncMock(
            side_effect=Exception("429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric queriesperday and limit Free Tier Limit")
        )
        with pytest.raises(GeminiQuotaExhaustedError) as exc_info:
            await service.get_embeddings(["Test input"])
        assert "project quota exhausted" in str(exc_info.value).lower()
        assert "aistudio.google.com" in str(exc_info.value).lower()
        backoff_sleeps_b = [s for s in sleep_calls if s >= 1.0]
        assert len(backoff_sleeps_b) == 0


# ---------------------------------------------------------------------------
# 7. Cancellation during backoff
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancellation_during_backoff():
    """Cancellation during retry sleep must be propagated cleanly."""
    service = GeminiService(api_key="mock_key")
    mock_client = MagicMock()
    mock_aio = MagicMock()
    mock_models = MagicMock()
    mock_client.aio = mock_aio
    mock_aio.models = mock_models

    err_429 = Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
    mock_models.embed_content = AsyncMock(side_effect=err_429)

    async def cancelling_sleep(duration):
        raise asyncio.CancelledError("Task was cancelled")

    with patch.object(service, "get_client", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=cancelling_sleep):
        with pytest.raises(asyncio.CancelledError):
            await service.get_embeddings(["Chunk to cancel"])


# ---------------------------------------------------------------------------
# 8. No partial promotion after embedding failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_partial_promotion_after_embedding_failure():
    """If embedding fails, chunks must not be committed to vector store."""
    temp_dir = Path(tempfile.mkdtemp(prefix="repotalk_test_db_"))
    try:
        store = VectorStore(db_path=temp_dir / "test_rag.db")
        repo_id = "test_rollback_repo"

        mock_chunks = [
            {"chunk_id": "c1", "file_path": "main.py", "chunk_text": "print('hello')", "content_hash": "h1"},
            {"chunk_id": "c2", "file_path": "server.py", "chunk_text": "listen()", "content_hash": "h2"},
        ]

        # Simulate Gemini failure during add_chunks
        with patch("backend.services.vector_store.gemini_service.get_embeddings",
                   side_effect=GeminiRateLimitError("Rate limit exhausted")):
            with pytest.raises(GeminiRateLimitError):
                await store.add_chunks(repo_id, mock_chunks)

        # Assert no partial chunks are committed
        count = store.get_chunk_count(repo_id)
        assert count == 0, f"Expected 0 committed chunks after failure, got {count}"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 9. Concurrent ingestion semaphore serialization
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_ingestion_semaphore_serialization():
    """Ingestion calls must acquire the ingestion semaphore to prevent concurrent bursts."""
    service = GeminiService(api_key="mock_key")
    active_calls = 0
    max_concurrent = 0

    async def mock_embed_batch_with_retry(*args, **kwargs):
        nonlocal active_calls, max_concurrent
        active_calls += 1
        max_concurrent = max(max_concurrent, active_calls)
        await asyncio.sleep(0.05)  # simulate provider latency
        active_calls -= 1
        return [[0.1] * 768]

    with patch.object(service, "_embed_batch_with_retry", side_effect=mock_embed_batch_with_retry), \
         patch.object(service, "get_client", return_value=MagicMock()):
        # Run two ingestion requests concurrently
        t1 = asyncio.create_task(service.get_embeddings(["Text 1"], is_ingestion=True))
        t2 = asyncio.create_task(service.get_embeddings(["Text 2"], is_ingestion=True))
        await asyncio.gather(t1, t2)

        # Max concurrent ingestion embedding operations must be 1
        assert max_concurrent == 1, f"Expected max 1 concurrent ingestion call, got {max_concurrent}"
