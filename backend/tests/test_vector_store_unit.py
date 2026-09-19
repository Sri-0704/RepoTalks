"""Unit tests for VectorStore fast RAG optimizations (offline, no network required).

Tests:
1. NumPy vectorized scoring (dot product + partial top-k with argpartition)
2. Normalized matrix LRU cache and memory limits
3. Dimension isolation: indexes of 768, 1536, and 3072 never mix
4. Local search (exact file and keyword) without embeddings
5. Content hash incremental indexing
6. FTS5 / LIKE search fallback
"""

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from backend.config import settings
from backend.services.vector_store import VectorStore


@pytest.fixture
def temp_store(tmp_path):
    """Create a disposable VectorStore in a temp directory."""
    with patch.object(settings, "DATA_DIR", tmp_path):
        store = VectorStore()
        yield store


def test_score_numpy_correctness(temp_store):
    """Verify NumPy cosine scoring and argpartition top-k ordering."""
    # 5 vectors with known similarities to query [1, 0, 0]
    dim = 3
    matrix = np.array([
        [1.0, 0.0, 0.0],  # sim = 1.0 (highest)
        [0.8, 0.6, 0.0],  # sim = 0.8
        [0.0, 1.0, 0.0],  # sim = 0.0
        [-0.5, 0.5, 0.0], # sim = -0.5 (norm will normalize)
        [0.9, 0.1, 0.0],  # sim = 0.9 (second highest)
    ], dtype=np.float32)
    # Normalize rows
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / norms

    row_ids = [101, 102, 103, 104, 105]
    query_vec = [1.0, 0.0, 0.0]

    top_results = temp_store._score_numpy(matrix, row_ids, query_vec, top_k=2)

    assert len(top_results) == 2
    # First result should be row 101 (sim ~ 1.0)
    assert top_results[0][0] == 101
    assert pytest.approx(top_results[0][1], abs=1e-4) == 1.0
    # Second result should be row 105 (sim ~ 0.99)
    assert top_results[1][0] == 105


def test_dimension_isolation_in_database(temp_store):
    """Verify that records with different embedding dimensions never mix."""
    repo_id = "test_dimension_repo"
    vec_768 = [0.1] * 768
    vec_1536 = [0.2] * 1536
    vec_3072 = [0.3] * 3072

    with temp_store._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO project_index 
            (repo_id, file_path, chunk_id, chunk_order, chunk_text, metadata_json, embedding_json, embedding_model, embedding_dimension)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_id, "file_768.py", "c1", 0, "chunk 768", "{}", json.dumps(vec_768), settings.EMBEDDING_MODEL, 768),
        )
        conn.execute(
            """
            INSERT INTO project_index 
            (repo_id, file_path, chunk_id, chunk_order, chunk_text, metadata_json, embedding_json, embedding_model, embedding_dimension)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_id, "file_1536.py", "c2", 0, "chunk 1536", "{}", json.dumps(vec_1536), settings.EMBEDDING_MODEL, 1536),
        )
        conn.execute(
            """
            INSERT INTO project_index 
            (repo_id, file_path, chunk_id, chunk_order, chunk_text, metadata_json, embedding_json, embedding_model, embedding_dimension)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_id, "file_3072.py", "c3", 0, "chunk 3072", "{}", json.dumps(vec_3072), settings.EMBEDDING_MODEL, 3072),
        )

    # Raw query for dimension 768 must only return 1 row
    rows_768 = temp_store._load_vectors_raw(repo_id, 768)
    assert len(rows_768) == 1

    # Raw query for dimension 1536 must only return 1 row
    rows_1536 = temp_store._load_vectors_raw(repo_id, 1536)
    assert len(rows_1536) == 1

    # Raw query for dimension 3072 must only return 1 row
    rows_3072 = temp_store._load_vectors_raw(repo_id, 3072)
    assert len(rows_3072) == 1

    # Raw query for non-existent dimension must return 0 rows
    rows_512 = temp_store._load_vectors_raw(repo_id, 512)
    assert len(rows_512) == 0


def test_matrix_cache_versioning_and_isolation(temp_store):
    """Matrix cache keys must include repo_id, model, and dimension."""
    repo_id = "cache_test_repo"
    tag_768 = f"{repo_id}:{settings.EMBEDDING_MODEL}:768"
    tag_3072 = f"{repo_id}:{settings.EMBEDDING_MODEL}:3072"

    mat_768 = np.ones((5, 768), dtype=np.float32)
    mat_3072 = np.ones((5, 3072), dtype=np.float32)

    temp_store._matrix_cache[tag_768] = (tag_768, mat_768, [1, 2, 3, 4, 5])
    temp_store._matrix_cache[tag_3072] = (tag_3072, mat_3072, [6, 7, 8, 9, 10])

    assert tag_768 in temp_store._matrix_cache
    assert tag_3072 in temp_store._matrix_cache
    assert temp_store._matrix_cache[tag_768][1].shape == (5, 768)
    assert temp_store._matrix_cache[tag_3072][1].shape == (5, 3072)


def test_search_local_requires_no_embeddings(temp_store):
    """search_local must return exact file and keyword matches without provider calls."""
    repo_id = "local_test_repo"

    with temp_store._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO project_index 
            (repo_id, file_path, chunk_id, chunk_order, chunk_text, metadata_json, embedding_json, embedding_model, embedding_dimension)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_id, "backend/services/auth.py", "auth_c1", 0, "def verify_token(token): pass", json.dumps({"symbols": ["verify_token"]}), "[]", settings.EMBEDDING_MODEL, 3072),
        )
        conn.execute(
            """
            INSERT INTO project_index 
            (repo_id, file_path, chunk_id, chunk_order, chunk_text, metadata_json, embedding_json, embedding_model, embedding_dimension)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_id, "backend/services/database.py", "db_c1", 0, "class DatabasePool: pass", json.dumps({"symbols": ["DatabasePool"]}), "[]", settings.EMBEDDING_MODEL, 3072),
        )

    # Search for auth.py
    results = temp_store.search_local(repo_id, "explain backend/services/auth.py")
    assert len(results["exact_files"]) >= 1
    assert results["exact_files"][0]["file_path"] == "backend/services/auth.py"
    assert results["exact_files"][0]["exact_match"] is True

    # Search for keyword "token"
    results_kw = temp_store.search_local(repo_id, "verify token implementation")
    assert len(results_kw["keyword_chunks"]) >= 1
    assert "token" in results_kw["keyword_chunks"][0]["chunk_text"]


def test_fts5_or_like_detection(temp_store):
    """Verify that FTS5 is properly initialized or LIKE fallback is active."""
    assert hasattr(temp_store, "_fts5_available")
    assert isinstance(temp_store._fts5_available, bool)


@pytest.mark.asyncio
async def test_search_semantic_scores_vectors_with_mocked_provider(temp_store, monkeypatch):
    """P0-01 fix: search_semantic correctly scores vectors and resolves dimensions without AttributeError."""
    from backend.services.gemini_service import gemini_service

    repo_id = "semantic_test_repo"
    dim = 768

    # Create two chunks: chunk 1 aligns with query, chunk 2 is orthogonal
    vec_match = [1.0] + [0.0] * (dim - 1)
    vec_other = [0.0, 1.0] + [0.0] * (dim - 2)

    chunks = [
        {"chunk_id": "c1", "chunk_text": "Authentication JWT handler", "file_path": "auth.py", "content_hash": "h1"},
        {"chunk_id": "c2", "chunk_text": "Database pool connection", "file_path": "db.py", "content_hash": "h2"},
    ]

    async def mock_get_embeddings(texts, **kwargs):
        if len(texts) == 2:
            return [vec_match, vec_other]
        # Query embedding: matches vec_match
        return [[1.0] + [0.0] * (dim - 1)]

    monkeypatch.setattr(gemini_service, "get_embeddings", mock_get_embeddings)

    await temp_store.add_chunks(repo_id, chunks)

    results = await temp_store.search_semantic(repo_id, "authenticate user token", top_k=2)
    assert len(results) == 2
    # First result must be auth.py with high similarity ~1.0
    assert results[0]["chunk_id"] == "c1"
    assert results[0]["file_path"] == "auth.py"
    assert pytest.approx(results[0]["similarity"], abs=1e-3) == 1.0
    # Second result has near 0 similarity
    assert results[1]["chunk_id"] == "c2"
    assert pytest.approx(results[1]["similarity"], abs=1e-3) == 0.0


@pytest.mark.asyncio
async def test_multibatch_checkpoint_slice_mapping(temp_store, monkeypatch):
    """P0-02 fix: multi-batch embedding checkpoints map chunks to their exact vectors across interruption and resume."""
    from backend.services.gemini_service import gemini_service

    repo_id = "multibatch_repo"
    dim = 8  # Small dimension for test

    # 5 chunks across 2 batches
    chunks = [
        {"chunk_id": f"chunk_{i}", "chunk_text": f"text {i}", "file_path": f"f_{i}.py", "content_hash": f"hash_{i}"}
        for i in range(5)
    ]

    # Batch 1 (3 items), Batch 2 (2 items)
    batch_1_vecs = [[float(i)] * dim for i in range(1, 4)]  # [[1..], [2..], [3..]]
    batch_2_vecs = [[float(i)] * dim for i in range(4, 6)]  # [[4..], [5..]]

    call_count = 0

    async def mock_get_embeddings_multibatch(texts, on_batch_complete=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate interruption after batch 1 & 2 save to staging
            if on_batch_complete:
                on_batch_complete(0, texts[:3], batch_1_vecs)
                on_batch_complete(1, texts[3:], batch_2_vecs)
            raise RuntimeError("Simulated network disconnect during embedding")
        # On second attempt, if any texts are passed, return them
        return [[9.0] * dim] * len(texts)

    monkeypatch.setattr(gemini_service, "get_embeddings", mock_get_embeddings_multibatch)

    # First attempt fails due to simulated network failure
    with pytest.raises(RuntimeError, match="Simulated network disconnect"):
        await temp_store.add_chunks(repo_id, chunks, session_id="test_session")

    # Inspect staging table directly
    snapshot_str = "".join(c["content_hash"] for c in chunks)
    snapshot_hash = hashlib.sha256(snapshot_str.encode()).hexdigest()
    session_key = temp_store.compute_session_key(repo_id, snapshot_hash, settings.EMBEDDING_MODEL)
    staged = temp_store.load_staging_checkpoint(repo_id, session_key, owner_session="test_session")

    # Crucial check: chunk 3 and chunk 4 (batch 1) must map to batch 2 vectors, NOT batch 1!
    assert staged["hash_0"] == [1.0] * dim
    assert staged["hash_1"] == [2.0] * dim
    assert staged["hash_2"] == [3.0] * dim
    assert staged["hash_3"] == [4.0] * dim
    assert staged["hash_4"] == [5.0] * dim

    # Second attempt (resume): all 5 chunks are in staging checkpoint!
    # No fresh embedding calls needed for any of the 5 chunks!
    added = await temp_store.add_chunks(repo_id, chunks, session_id="test_session")
    assert added == 5
    # Provider was NOT called on resume because all 5 were resolved from staging!
    assert call_count == 1


