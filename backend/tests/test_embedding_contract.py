"""Embedding contract test — verifies Gemini embedding behavior.

Run with:  python -m pytest backend/tests/test_embedding_contract.py -v

Requires a valid GEMINI_API_KEY in the environment or .env file.
Budget: a small number of embedding calls for contract validation.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.services.gemini_service import gemini_service, GeminiServiceError


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _has_api_key() -> bool:
    if os.getenv("RUN_LIVE_GEMINI_TESTS") != "1":
        return False
    from backend.config import settings
    key = os.getenv("GEMINI_API_KEY") or settings.GEMINI_API_KEY or ""
    # Support both traditional AIzaSy keys and new Google AI Studio AQ. auth keys
    return bool(key and (key.startswith("AIzaSy") or key.startswith("AQ.")))


skip_no_key = pytest.mark.skipif(
    not _has_api_key(),
    reason="Live Gemini tests are strictly opt-in to preserve quota (set RUN_LIVE_GEMINI_TESTS=1 to enable).",
)


@skip_no_key
@pytest.mark.asyncio
async def test_single_text_returns_one_embedding():
    """A single text input must return exactly one embedding vector."""
    result = await gemini_service.get_embeddings(["Hello world"])
    assert len(result) == 1
    assert isinstance(result[0], list)
    assert len(result[0]) > 0
    assert all(isinstance(v, float) for v in result[0])


@skip_no_key
@pytest.mark.asyncio
async def test_cardinality_matches_input():
    """Number of output embeddings must equal number of input texts."""
    texts = ["Alpha", "Beta", "Gamma", "Delta"]
    result = await gemini_service.get_embeddings(texts)
    assert len(result) == len(texts), f"Expected {len(texts)} embeddings, got {len(result)}"


@skip_no_key
@pytest.mark.asyncio
async def test_consistent_dimensions():
    """All embeddings in a single batch must have the same dimensionality."""
    texts = ["Hello", "World", "Test embedding dimensions"]
    result = await gemini_service.get_embeddings(texts)
    dimensions = {len(emb) for emb in result}
    assert len(dimensions) == 1, f"Inconsistent dimensions: {dimensions}"


@skip_no_key
@pytest.mark.asyncio
async def test_independent_embeddings():
    """Each text must produce an independent embedding (not all identical)."""
    texts = [
        "Python is a programming language",
        "The weather is sunny today",
        "Quantum mechanics describes subatomic particles",
    ]
    result = await gemini_service.get_embeddings(texts)
    # Pairwise comparison — at least some values should differ
    for i in range(len(result)):
        for j in range(i + 1, len(result)):
            assert result[i] != result[j], f"Embedding {i} is identical to embedding {j}"


@skip_no_key
@pytest.mark.asyncio
async def test_ordering_is_deterministic():
    """Same input twice should produce the same ordering of embeddings."""
    texts = ["Alpha", "Beta"]
    result_a = await gemini_service.get_embeddings(texts)
    result_b = await gemini_service.get_embeddings(texts)
    # The embeddings should be in the same order (first text -> first embedding)
    # We check that result_a[0] is closer to result_b[0] than to result_b[1]
    import math
    def cosine_sim(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    sim_same = cosine_sim(result_a[0], result_b[0])
    sim_cross = cosine_sim(result_a[0], result_b[1])
    assert sim_same > sim_cross, f"Ordering not preserved: same={sim_same:.4f}, cross={sim_cross:.4f}"


@skip_no_key
@pytest.mark.asyncio
async def test_empty_input_returns_empty():
    """Empty text list must return empty list without calling the API."""
    result = await gemini_service.get_embeddings([])
    assert result == []


@skip_no_key
@pytest.mark.asyncio
async def test_output_dimensionality_parameter():
    """When output_dimensionality is set, vectors should have that dimension."""
    texts = ["Test dimensionality control"]
    result_default = await gemini_service.get_embeddings(texts)
    result_768 = await gemini_service.get_embeddings(texts, output_dimensionality=768)

    default_dim = len(result_default[0])
    reduced_dim = len(result_768[0])

    assert reduced_dim == 768, f"Expected 768 dimensions, got {reduced_dim}"
    # Default should be larger (3072 for Gemini Embedding 2)
    assert default_dim >= reduced_dim, f"Default dim {default_dim} should be >= reduced dim {reduced_dim}"
