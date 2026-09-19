"""Cache service for RepoTalk.

Provides bounded, in-memory LRU caches with TTL and repo-invalidation for:
1. Query embeddings (avoiding redundant remote API calls for repeated/similar queries)
2. Retrieval search results (caching top-k results per repo_id and query)
3. Feature artifacts (architecture graphs, question banks, audience summaries)
"""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LRUCache:
    """Thread-safe bounded LRU cache with optional TTL."""

    def __init__(self, maxsize: int = 500, default_ttl_seconds: Optional[float] = None):
        self._maxsize = maxsize
        self._default_ttl = default_ttl_seconds
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._cache:
                return None
            val, expire_time = self._cache[key]
            if expire_time > 0 and time.time() > expire_time:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return val

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expire_time = (time.time() + ttl) if ttl else 0.0
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, expire_time)
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._cache.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def delete_prefix(self, prefix: str) -> int:
        with self._lock:
            keys_to_del = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_del:
                del self._cache[k]
            return len(keys_to_del)


class CacheService:
    def __init__(self):
        # Query embedding cache: capacity 1000, 2 hour TTL
        self._embedding_cache = LRUCache(maxsize=1000, default_ttl_seconds=7200)
        # Retrieval result cache: capacity 300, 5 minute TTL
        self._retrieval_cache = LRUCache(maxsize=300, default_ttl_seconds=300)
        # Feature artifact cache (architecture, viva, audience): capacity 100, 1 hour TTL
        self._artifact_cache = LRUCache(maxsize=100, default_ttl_seconds=3600)

    # ------------------------------------------------------------------
    # Query Embedding Cache
    # ------------------------------------------------------------------

    @staticmethod
    def _embedding_key(model: str, dimension: int, text: str) -> str:
        h = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:24]
        return f"emb:{model}:{dimension}:{h}"

    def get_query_embedding(self, model: str, dimension: int, text: str) -> Optional[List[float]]:
        key = self._embedding_key(model, dimension, text)
        return self._embedding_cache.get(key)

    def set_query_embedding(self, model: str, dimension: int, text: str, vector: List[float]) -> None:
        key = self._embedding_key(model, dimension, text)
        self._embedding_cache.set(key, vector)

    # ------------------------------------------------------------------
    # Retrieval Result Cache
    # ------------------------------------------------------------------

    @staticmethod
    def _retrieval_key(repo_id: str, mode: str, query: str, top_k: int) -> str:
        qh = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:16]
        return f"ret:{repo_id}:{mode}:{top_k}:{qh}"

    def get_retrieval(self, repo_id: str, mode: str, query: str, top_k: int) -> Optional[List[Dict[str, Any]]]:
        key = self._retrieval_key(repo_id, mode, query, top_k)
        return self._retrieval_cache.get(key)

    def set_retrieval(self, repo_id: str, mode: str, query: str, top_k: int, results: List[Dict[str, Any]]) -> None:
        key = self._retrieval_key(repo_id, mode, query, top_k)
        self._retrieval_cache.set(key, results)

    # ------------------------------------------------------------------
    # Artifact Cache
    # ------------------------------------------------------------------

    @staticmethod
    def _artifact_key(repo_id: str, feature: str, subkey: str = "default") -> str:
        return f"art:{repo_id}:{feature}:{subkey}"

    def get_artifact(self, repo_id: str, feature: str, subkey: str = "default") -> Optional[Any]:
        key = self._artifact_key(repo_id, feature, subkey)
        return self._artifact_cache.get(key)

    def set_artifact(self, repo_id: str, feature: str, subkey: str = "default", data: Any = None) -> None:
        key = self._artifact_key(repo_id, feature, subkey)
        self._artifact_cache.set(key, data)

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate_repo(self, repo_id: str) -> None:
        """Invalidate all retrieval and artifact cache entries when a repo is re-indexed or updated."""
        ret_del = self._retrieval_cache.delete_prefix(f"ret:{repo_id}:")
        art_del = self._artifact_cache.delete_prefix(f"art:{repo_id}:")
        logger.info("Invalidated caches for repo '%s' (%d retrieval, %d artifact entries)", repo_id, ret_del, art_del)

    def clear_all(self) -> None:
        self._embedding_cache.clear()
        self._retrieval_cache.clear()
        self._artifact_cache.clear()
        logger.info("Cleared all caches")


cache_service = CacheService()
