import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import struct
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np

from backend.config import settings
from backend.services.cache_service import cache_service
from backend.services.gemini_service import gemini_service

logger = logging.getLogger(__name__)


class VectorStore:
    """SQLite-backed project index with split search modes.

    search_local()    — file/symbol matching only, no embeddings, no network.
    search_semantic() — embedding + cosine similarity over stored vectors.
    search_hybrid()   — local first, then semantic for remaining slots.
    search()          — backward-compatible hybrid (used by existing services).
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else (settings.DATA_DIR / "db" / "vector_store.db")
        self.attached_repo_id: Optional[str] = None
        # Normalized matrix LRU cache: repo_id -> (version_tag, matrix, ids)
        self._matrix_cache: OrderedDict[str, Tuple[str, np.ndarray, List[int]]] = OrderedDict()
        self._matrix_cache_max_bytes = 64 * 1024 * 1024  # 64 MiB budget
        self._init_db()

    def ensure_tables(self) -> None:
        """Public method to verify or initialize database schema."""
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    chunk_order INTEGER NOT NULL DEFAULT 0,
                    chunk_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    embedding_model TEXT,
                    embedding_dimension INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_index)")}
            for column, definition in {
                "chunk_order": "INTEGER NOT NULL DEFAULT 0",
                "embedding_model": "TEXT",
                "embedding_dimension": "INTEGER",
                "content_hash": "TEXT",
                "index_generation": "INTEGER NOT NULL DEFAULT 1",
            }.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE project_index ADD COLUMN {column} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repo_id ON project_index(repo_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repo_file ON project_index(repo_id, file_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON project_index(content_hash, embedding_model, embedding_dimension)")

            # Staging table for cross-attempt resume (never searchable, bounded TTL)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_staging (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    batch_index INTEGER NOT NULL,
                    chunk_id TEXT NOT NULL,
                    chunk_order INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding_dimension INTEGER NOT NULL,
                    owner_session TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    UNIQUE(session_key, chunk_id)
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_lookup ON embedding_staging(session_key, content_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_expires ON embedding_staging(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_repo ON embedding_staging(repo_id)")

            # Clean expired staging entries on startup
            try:
                self.clean_expired_staging(conn)
            except Exception as e:
                logger.warning("Failed to clean expired staging on init: %s", e)

            # Check FTS5 availability
            self._fts5_available = self._check_fts5(conn)
            if self._fts5_available:
                self._init_fts5(conn)
                logger.info("FTS5 full-text search is available and initialized")
            else:
                logger.warning("FTS5 not available in this SQLite build — falling back to LIKE substring search")

    @staticmethod
    def _check_fts5(conn: sqlite3.Connection) -> bool:
        """Check if FTS5 extension is available."""
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(content)")
            conn.execute("DROP TABLE IF EXISTS _fts5_check")
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def _init_fts5(conn: sqlite3.Connection) -> None:
        """Create FTS5 virtual table and synchronization triggers for full-text search."""
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS project_fts USING fts5(
                chunk_text,
                content='project_index',
                content_rowid='id',
                tokenize='unicode61'
            );
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS project_index_ai AFTER INSERT ON project_index BEGIN
                INSERT INTO project_fts(rowid, chunk_text) VALUES (new.id, new.chunk_text);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS project_index_ad AFTER DELETE ON project_index BEGIN
                INSERT INTO project_fts(project_fts, rowid, chunk_text) VALUES('delete', old.id, old.chunk_text);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS project_index_au AFTER UPDATE ON project_index BEGIN
                INSERT INTO project_fts(project_fts, rowid, chunk_text) VALUES('delete', old.id, old.chunk_text);
                INSERT INTO project_fts(rowid, chunk_text) VALUES (new.id, new.chunk_text);
            END;
            """
        )

    # ------------------------------------------------------------------
    # Staging Checkpoint Management (Point 10)
    # ------------------------------------------------------------------

    CHECKPOINT_VERSION: str = "v2"

    @classmethod
    def compute_session_key(
        cls,
        repo_id: str,
        snapshot_hash: str,
        model: str,
        dimension: Optional[int] = None,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> str:
        """Compute deterministic session key for staging checkpoints."""
        raw = f"{cls.CHECKPOINT_VERSION}:{repo_id}:{snapshot_hash}:{model}:{dimension or 0}:{task_type}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def clean_expired_staging(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """Remove expired staging entries and enforce max rows ceiling."""
        if conn is not None:
            return self._do_clean_staging(conn)
        with self._get_connection() as c:
            return self._do_clean_staging(c)

    def _do_clean_staging(self, conn: sqlite3.Connection) -> int:
        cursor = conn.execute(
            "DELETE FROM embedding_staging WHERE expires_at <= CURRENT_TIMESTAMP"
        )
        deleted = cursor.rowcount
        max_rows = settings.EMBEDDING_CHECKPOINT_MAX_ROWS
        total_row = conn.execute("SELECT COUNT(*) AS cnt FROM embedding_staging").fetchone()
        total = total_row["cnt"] if total_row else 0
        if total > max_rows:
            overflow = total - max_rows
            conn.execute(
                """
                DELETE FROM embedding_staging WHERE id IN (
                    SELECT id FROM embedding_staging ORDER BY created_at ASC LIMIT ?
                )
                """,
                (overflow,),
            )
            deleted += overflow
        return deleted

    def save_staging_batch(
        self,
        repo_id: str,
        session_key: str,
        batch_index: int,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
        owner_session: str = "default",
        ttl_hours: Optional[int] = None,
    ) -> None:
        """Save a validated batch of embeddings to staging checkpoint.

        Checkpoints are never searchable and expire after configured TTL.
        """
        hours = ttl_hours if ttl_hours is not None else settings.EMBEDDING_CHECKPOINT_TTL_HOURS
        if hours <= 0 or not chunks or not embeddings:
            return

        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Count mismatch in save_staging_batch: {len(chunks)} chunks vs {len(embeddings)} embeddings"
            )
        batch_dim = len(embeddings[0]) if (embeddings and embeddings[0]) else 0
        if any(len(e) != batch_dim for e in embeddings):
            raise ValueError("Inconsistent embedding dimensions in save_staging_batch")

        expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        records = []
        for chunk, emb in zip(chunks, embeddings):
            records.append((
                repo_id,
                session_key,
                batch_index,
                str(chunk.get("chunk_id", "")),
                int(chunk.get("chunk_order", 0)),
                chunk.get("content_hash", ""),
                json.dumps(emb),
                settings.EMBEDDING_MODEL,
                len(emb),
                owner_session,
                expires_at,
            ))

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO embedding_staging (
                    repo_id, session_key, batch_index, chunk_id, chunk_order,
                    content_hash, embedding_json, embedding_model, embedding_dimension,
                    owner_session, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )

    def load_staging_checkpoint(
        self,
        repo_id: str,
        session_key: str,
        owner_session: Optional[str] = None,
    ) -> Dict[str, List[float]]:
        """Load previously staged embeddings matching session_key.

        Validates session_key, ownership, and expiry.
        Returns mapping from content_hash -> embedding_vector.
        """
        if settings.EMBEDDING_CHECKPOINT_TTL_HOURS <= 0:
            return {}

        query = """
            SELECT content_hash, embedding_json, embedding_dimension
            FROM embedding_staging
            WHERE repo_id = ? AND session_key = ? AND expires_at > CURRENT_TIMESTAMP
        """
        params: list[Any] = [repo_id, session_key]
        if owner_session:
            query += " AND owner_session = ?"
            params.append(owner_session)

        staged: Dict[str, List[float]] = {}
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            for r in rows:
                ch = r["content_hash"]
                if ch and ch not in staged:
                    try:
                        staged[ch] = json.loads(r["embedding_json"])
                    except Exception:
                        pass
        return staged

    def clear_staging(self, repo_id: str, session_key: Optional[str] = None) -> None:
        """Clear staging rows for a repository (called after successful promotion or on deletion)."""
        with self._get_connection() as conn:
            if session_key:
                conn.execute(
                    "DELETE FROM embedding_staging WHERE repo_id = ? AND session_key = ?",
                    (repo_id, session_key),
                )
            else:
                conn.execute("DELETE FROM embedding_staging WHERE repo_id = ?", (repo_id,))

    def get_chunk_count(self, repo_id: str, compatible_only: bool = False) -> int:
        query = "SELECT COUNT(*) AS cnt FROM project_index WHERE repo_id = ?"
        params: list[Any] = [repo_id]
        if compatible_only:
            query += " AND embedding_model = ? AND embedding_dimension IS NOT NULL"
            params.append(settings.EMBEDDING_MODEL)
        with self._get_connection() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row["cnt"]) if row else 0

    def is_index_attached(self, repo_id: str) -> bool:
        return self.attached_repo_id == repo_id and self.get_chunk_count(repo_id, compatible_only=True) > 0

    def attach_project_index(self, repo_id: str) -> Dict[str, Any]:
        count = self.get_chunk_count(repo_id, compatible_only=True)
        self.attached_repo_id = repo_id
        logger.info("Attached RAG index for repo '%s' with %s compatible chunks", repo_id, count)
        return {"attached": True, "repo_id": repo_id, "chunk_count": count}

    # ------------------------------------------------------------------
    # Local search — no network, no embeddings
    # ------------------------------------------------------------------

    def search_local(
        self,
        repo_id: str,
        query: str,
        top_k: int = 10,
        context_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return file and keyword matches without any embedding call.

        Returns {"exact_files": [...], "keyword_chunks": [...]}.
        Works even when the Gemini API is unavailable.
        """
        exact = self._get_exact_file_chunks(repo_id, query, top_k, context_file)
        keyword = self._get_keyword_chunks(repo_id, query, top_k)
        return {"exact_files": exact, "keyword_chunks": keyword}

    def _get_exact_file_chunks(self, repo_id: str, query: str, top_k: int, context_file: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT file_path FROM project_index WHERE repo_id = ? AND embedding_model = ?",
                (repo_id, settings.EMBEDDING_MODEL),
            ).fetchall()
        matched_paths = set()
        query_lower = query.lower()
        for row in rows:
            file_path = row["file_path"]
            normalized = file_path.replace("\\", "/")
            if context_file and normalized.lower() == context_file.replace("\\", "/").lower():
                matched_paths.add(file_path)
            elif normalized.lower() in query_lower or Path(normalized).name.lower() in query_lower:
                matched_paths.add(file_path)
        if not matched_paths:
            return []
        placeholders = ",".join("?" for _ in matched_paths)
        params: list[Any] = [repo_id, settings.EMBEDDING_MODEL, *sorted(matched_paths), top_k]
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT file_path, chunk_id, chunk_text, metadata_json
                FROM project_index
                WHERE repo_id = ? AND embedding_model = ? AND file_path IN ({placeholders})
                ORDER BY file_path, chunk_order ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {"file_path": row["file_path"], "chunk_id": row["chunk_id"], "chunk_text": row["chunk_text"],
             "metadata": json.loads(row["metadata_json"]), "similarity": 1.0, "exact_match": True}
            for row in rows
        ]

    def _get_keyword_chunks(self, repo_id: str, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Full-text search using FTS5 if available, falling back to LIKE."""
        if self._fts5_available:
            return self._get_keyword_chunks_fts5(repo_id, query, top_k)
        return self._get_keyword_chunks_like(repo_id, query, top_k)

    def _get_keyword_chunks_fts5(self, repo_id: str, query: str, top_k: int) -> List[Dict[str, Any]]:
        """FTS5/BM25 ranked full-text search."""
        stop_words = {"what", "where", "when", "which", "who", "how", "why", "the", "and", "or", "but", "with", "about", "from", "into", "this", "that", "these", "those"}
        terms = [word.strip() for word in query.lower().replace("_", " ").replace("-", " ").split() if len(word.strip()) >= 3 and word.strip() not in stop_words][:5]
        if not terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in terms)
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT p.file_path, p.chunk_id, p.chunk_text, p.metadata_json,
                           rank AS bm25_score
                    FROM project_fts f
                    JOIN project_index p ON f.rowid = p.id
                    WHERE project_fts MATCH ? AND p.repo_id = ? AND p.embedding_model = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, repo_id, settings.EMBEDDING_MODEL, top_k),
                ).fetchall()
            return [
                {"file_path": row["file_path"], "chunk_id": row["chunk_id"], "chunk_text": row["chunk_text"],
                 "metadata": json.loads(row["metadata_json"]), "similarity": 0.85, "keyword_match": True}
                for row in rows
            ]
        except sqlite3.OperationalError:
            # FTS table may not be populated yet — fall back to LIKE
            return self._get_keyword_chunks_like(repo_id, query, top_k)

    def _get_keyword_chunks_like(self, repo_id: str, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Fallback substring search when FTS5 is unavailable."""
        stop_words = {"what", "where", "when", "which", "who", "how", "why", "the", "and", "or", "but", "with", "about", "from", "into", "this", "that", "these", "those"}
        terms = [word.strip() for word in query.lower().replace("_", " ").replace("-", " ").split() if len(word.strip()) >= 3 and word.strip() not in stop_words][:5]
        if not terms:
            return []
        conditions = " OR ".join("LOWER(chunk_text) LIKE ?" for _ in terms)
        params = [repo_id, settings.EMBEDDING_MODEL, *[f"%{term}%" for term in terms], top_k]
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT file_path, chunk_id, chunk_text, metadata_json FROM project_index
                WHERE repo_id = ? AND embedding_model = ? AND ({conditions})
                ORDER BY file_path, chunk_order ASC LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {"file_path": row["file_path"], "chunk_id": row["chunk_id"], "chunk_text": row["chunk_text"],
             "metadata": json.loads(row["metadata_json"]), "similarity": 0.85, "keyword_match": True}
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Semantic search — requires embeddings
    # ------------------------------------------------------------------

    async def search_semantic(
        self,
        repo_id: str,
        query: str,
        top_k: int = 5,
        override_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Resolve target dimension: check what dimension this repository was actually indexed with
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT embedding_dimension FROM project_index WHERE repo_id = ? AND embedding_model = ? AND embedding_dimension IS NOT NULL LIMIT 1",
                (repo_id, settings.EMBEDDING_MODEL),
            ).fetchone()
        repo_dim = row["embedding_dimension"] if row else None
        dim_target = repo_dim or getattr(settings, "EMBEDDING_DIMENSION", 768) or 768

        # Check query embedding cache
        cached_vec = cache_service.get_query_embedding(settings.EMBEDDING_MODEL, dim_target, query)
        if cached_vec is not None:
            query_embedding = cached_vec
            dimension = len(query_embedding)
        else:
            embeddings = await gemini_service.get_embeddings(
                [query],
                override_key=override_key,
                output_dimensionality=dim_target,
            )
            if not embeddings or not embeddings[0]:
                return []
            query_embedding = embeddings[0]
            dimension = len(query_embedding)
            cache_service.set_query_embedding(settings.EMBEDDING_MODEL, dimension, query, query_embedding)

        # Load and score using NumPy
        matrix, row_ids = await self._get_matrix(repo_id, dimension)
        if matrix is None or len(row_ids) == 0:
            return []

        scored = await asyncio.to_thread(self._score_numpy, matrix, row_ids, query_embedding, top_k)
        return self._load_chunk_details(scored)

    def _score_numpy(
        self,
        matrix: np.ndarray,
        row_ids: List[int],
        query_vec: List[float],
        top_k: int,
    ) -> List[Tuple[int, float]]:
        """Score all vectors against query using NumPy dot product.

        Returns list of (row_id, similarity) for top-k results.
        """
        q = np.array(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q = q / q_norm

        # Matrix is already L2-normalized in _get_matrix
        scores = matrix @ q  # (n,) dot products = cosine similarities

        # Partial top-k using argpartition (O(n) vs O(n log n) for full sort)
        k = min(top_k, len(scores))
        if k <= 0:
            return []
        top_indices = np.argpartition(scores, -k)[-k:]
        top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        return [(row_ids[i], float(scores[i])) for i in top_indices]

    async def _get_matrix(self, repo_id: str, dimension: int) -> Tuple[Optional[np.ndarray], List[int]]:
        """Load normalized float32 matrix from cache or database."""
        version_tag = f"{repo_id}:{settings.EMBEDDING_MODEL}:{dimension}"
        if version_tag in self._matrix_cache:
            self._matrix_cache.move_to_end(version_tag)
            cached = self._matrix_cache[version_tag]
            return cached[1], cached[2]

        # Load from database
        rows = await asyncio.to_thread(self._load_vectors_raw, repo_id, dimension)
        if not rows:
            return None, []

        # Preallocate numpy buffer to eliminate intermediate list-of-lists float allocations
        n_rows = len(rows)
        matrix_buf = np.empty((n_rows, dimension), dtype=np.float32)
        row_ids: List[int] = []
        valid_idx = 0
        for row in rows:
            try:
                vec = json.loads(row["embedding_json"])
                if len(vec) == dimension:
                    matrix_buf[valid_idx] = vec
                    row_ids.append(row["id"])
                    valid_idx += 1
            except (json.JSONDecodeError, TypeError):
                continue

        if valid_idx == 0:
            return None, []

        matrix = matrix_buf[:valid_idx]
        # L2 normalize each row
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms

        # Check memory budget before caching
        matrix_bytes = matrix.nbytes
        total_cached = sum(v[1].nbytes for v in self._matrix_cache.values())
        if total_cached + matrix_bytes > self._matrix_cache_max_bytes:
            # Evict least recently used entries (FIFO from head of OrderedDict)
            while self._matrix_cache and total_cached + matrix_bytes > self._matrix_cache_max_bytes:
                _, evicted = self._matrix_cache.popitem(last=False)
                total_cached -= evicted[1].nbytes

        self._matrix_cache[version_tag] = (version_tag, matrix, row_ids)
        self._matrix_cache.move_to_end(version_tag)
        logger.info("Cached %d vectors (%d dims, %.1f MiB) for %s", len(row_ids), dimension, matrix_bytes / 1024 / 1024, repo_id)
        return matrix, row_ids

    def _load_vectors_raw(self, repo_id: str, dimension: int) -> List[sqlite3.Row]:
        with self._get_connection() as conn:
            return conn.execute(
                """
                SELECT id, embedding_json FROM project_index
                WHERE repo_id = ? AND embedding_model = ? AND embedding_dimension = ?
                """,
                (repo_id, settings.EMBEDDING_MODEL, dimension),
            ).fetchall()

    def _load_chunk_details(self, scored: List[Tuple[int, float]]) -> List[Dict[str, Any]]:
        """Load text and metadata for scored row IDs."""
        if not scored:
            return []
        ids = [row_id for row_id, _ in scored]
        placeholders = ",".join("?" for _ in ids)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, file_path, chunk_id, chunk_text, metadata_json
                FROM project_index WHERE id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        row_map = {row["id"]: row for row in rows}
        results = []
        for row_id, sim in scored:
            row = row_map.get(row_id)
            if row:
                results.append({
                    "file_path": row["file_path"],
                    "chunk_id": row["chunk_id"],
                    "chunk_text": row["chunk_text"],
                    "metadata": json.loads(row["metadata_json"]),
                    "similarity": round(sim, 4),
                })
        return results

    # ------------------------------------------------------------------
    # Hybrid search — combines local + semantic
    # ------------------------------------------------------------------

    async def search(
        self,
        repo_id: str,
        query: str,
        top_k: int = 5,
        context_file: Optional[str] = None,
        override_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Backward-compatible hybrid search.

        1. Get exact file matches and keyword matches (no network).
        2. If those already fill top_k, skip the embedding call.
        3. Otherwise, run semantic search for remaining slots.
        """
        # Check retrieval cache
        cached_res = cache_service.get_retrieval(repo_id, "hybrid", f"{query}:{context_file or ''}", top_k)
        if cached_res is not None:
            return cached_res

        exact_chunks = self._get_exact_file_chunks(repo_id, query, top_k, context_file)
        keyword_chunks = self._get_keyword_chunks(repo_id, query, top_k)

        # Deduplicate local results
        seen = set()
        local_combined: List[Dict[str, Any]] = []
        for chunk in [*exact_chunks, *keyword_chunks]:
            key = (chunk["file_path"], chunk["chunk_id"])
            if key not in seen:
                seen.add(key)
                local_combined.append(chunk)

        # Early return: if local results fill top_k, skip embedding
        if len(local_combined) >= top_k:
            result = local_combined[:top_k]
            cache_service.set_retrieval(repo_id, "hybrid", f"{query}:{context_file or ''}", top_k, result)
            return result

        # Semantic search for remaining slots
        remaining = top_k - len(local_combined)
        try:
            semantic_chunks = await self.search_semantic(repo_id, query, top_k=remaining + 3, override_key=override_key)
        except Exception as exc:
            logger.warning("Semantic search failed, returning local results only: %s", exc)
            result = local_combined[:top_k]
            cache_service.set_retrieval(repo_id, "hybrid", f"{query}:{context_file or ''}", top_k, result)
            return result

        for chunk in semantic_chunks:
            key = (chunk["file_path"], chunk["chunk_id"])
            if key not in seen:
                seen.add(key)
                local_combined.append(chunk)

        result = local_combined[:top_k]
        cache_service.set_retrieval(repo_id, "hybrid", f"{query}:{context_file or ''}", top_k, result)
        return result

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Write path with Staging Checkpoints and Atomic Promotion (Points 10, 11)
    # ------------------------------------------------------------------

    async def add_chunks(
        self,
        repo_id: str,
        chunks: List[Dict[str, Any]],
        override_key: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
        session_id: Optional[str] = None,
        snapshot_hash: Optional[str] = None,
    ) -> int:
        if not chunks:
            return 0

        # Derive session key for staging checkpoints
        if not snapshot_hash:
            snapshot_str = "".join(c.get("content_hash", "") for c in chunks)
            snapshot_hash = hashlib.sha256(snapshot_str.encode()).hexdigest()
        session_key = self.compute_session_key(repo_id, snapshot_hash, settings.EMBEDDING_MODEL)

        # 1. Check existing committed index for identical content hashes
        cached_embeddings: Dict[str, List[float]] = {}
        unique_hashes = list({c.get("content_hash") for c in chunks if c.get("content_hash")})

        if unique_hashes:
            with self._get_connection() as conn:
                for i in range(0, len(unique_hashes), 500):
                    batch = unique_hashes[i : i + 500]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        f"""
                        SELECT content_hash, embedding_json, embedding_dimension
                        FROM project_index
                        WHERE embedding_model = ? AND content_hash IN ({placeholders})
                        """,
                        (settings.EMBEDDING_MODEL, *batch),
                    ).fetchall()
                    for row in rows:
                        h = row["content_hash"]
                        dim = row["embedding_dimension"]
                        if h and h not in cached_embeddings and dim:
                            try:
                                cached_embeddings[h] = json.loads(row["embedding_json"])
                            except Exception:
                                pass

        # 2. Check staging checkpoints (cross-attempt resume)
        staged_embeddings = self.load_staging_checkpoint(repo_id, session_key, owner_session=session_id)

        # 3. Determine which chunks need fresh provider embedding
        chunks_to_embed: List[str] = []
        chunks_to_embed_indices: List[int] = []
        chunks_to_embed_items: List[Dict[str, Any]] = []
        final_embeddings: List[Optional[List[float]]] = [None] * len(chunks)

        for idx, chunk in enumerate(chunks):
            h = chunk.get("content_hash")
            if h and h in cached_embeddings:
                final_embeddings[idx] = cached_embeddings[h]
            elif h and h in staged_embeddings:
                final_embeddings[idx] = staged_embeddings[h]
            else:
                chunks_to_embed.append(chunk["chunk_text"])
                chunks_to_embed_indices.append(idx)
                chunks_to_embed_items.append(chunk)

        # 4. Embed only uncached chunks, checkpointing each completed batch to staging
        if chunks_to_embed:
            current_batch_offset = 0

            def _on_batch_complete(batch_idx: int, batch_texts: List[str], batch_embs: List[List[float]]) -> None:
                nonlocal current_batch_offset
                batch_len = len(batch_texts)
                if len(batch_embs) != batch_len:
                    logger.error(
                        "Batch embedding count mismatch for batch %d: %d texts vs %d embeddings",
                        batch_idx,
                        batch_len,
                        len(batch_embs),
                    )
                    return

                batch_chunk_items = chunks_to_embed_items[current_batch_offset : current_batch_offset + batch_len]
                current_batch_offset += batch_len

                try:
                    # Stash batch results to staging checkpoint immediately
                    self.save_staging_batch(
                        repo_id=repo_id,
                        session_key=session_key,
                        batch_index=batch_idx,
                        chunks=batch_chunk_items,
                        embeddings=batch_embs,
                        owner_session=session_id or "default",
                    )
                except Exception as e:
                    logger.warning("Failed to save intermediate staging batch: %s", e)

            new_embeddings = await gemini_service.get_embeddings(
                chunks_to_embed,
                override_key=override_key,
                is_ingestion=True,
                progress_cb=progress_cb,
                on_batch_complete=_on_batch_complete,
            )
            if len(new_embeddings) != len(chunks_to_embed) or not new_embeddings or not new_embeddings[0]:
                raise ValueError("Embedding provider returned an incomplete index.")
            for slot_idx, emb in zip(chunks_to_embed_indices, new_embeddings):
                final_embeddings[slot_idx] = emb

            # Final staging checkpoint for all new embeddings
            try:
                self.save_staging_batch(
                    repo_id=repo_id,
                    session_key=session_key,
                    batch_index=0,
                    chunks=chunks_to_embed_items,
                    embeddings=new_embeddings,
                    owner_session=session_id or "default",
                )
            except Exception as e:
                logger.warning("Failed to save staging checkpoint: %s", e)

        if not final_embeddings or final_embeddings[0] is None:
            raise ValueError("No embeddings could be resolved for indexing.")

        dimension = len(final_embeddings[0])
        if any(emb is None or len(emb) != dimension for emb in final_embeddings):
            raise ValueError("Embedding provider returned inconsistent vector dimensions.")

        # 5. Two-Phase Atomic Index Promotion (Point 11)
        # Allocate new generation tag. Existing index remains untouched if promotion fails.
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(index_generation), 0) AS max_gen FROM project_index WHERE repo_id = ?",
                (repo_id,),
            ).fetchone()
            current_gen = row["max_gen"] if row else 0
            next_gen = current_gen + 1

            records = [
                (
                    repo_id,
                    chunk.get("file_path", "unknown").replace("\\", "/"),
                    str(chunk.get("chunk_id", index)),
                    int(chunk.get("chunk_order", index)),
                    chunk["chunk_text"],
                    json.dumps(chunk.get("metadata", {})),
                    json.dumps(embedding),
                    settings.EMBEDDING_MODEL,
                    dimension,
                    chunk.get("content_hash", ""),
                    next_gen,
                )
                for index, (chunk, embedding) in enumerate(zip(chunks, final_embeddings))
            ]

            # Phase 1: Insert new-generation rows
            conn.executemany(
                """
                INSERT INTO project_index (
                    repo_id, file_path, chunk_id, chunk_order, chunk_text, metadata_json,
                    embedding_json, embedding_model, embedding_dimension, content_hash,
                    index_generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )

            # Phase 2: Atomically swap — delete previous generation(s) for this repo
            conn.execute(
                "DELETE FROM project_index WHERE repo_id = ? AND index_generation <= ?",
                (repo_id, current_gen),
            )

            # Rebuild FTS index if available
            if self._fts5_available:
                try:
                    conn.execute("INSERT INTO project_fts(project_fts) VALUES('rebuild')")
                except sqlite3.OperationalError:
                    logger.warning("FTS5 rebuild failed — full-text search may be stale")

        # 6. Cleanup staging checkpoints after successful promotion
        self.clear_staging(repo_id, session_key)

        # Invalidate matrix and retrieval caches for this repo
        keys_to_remove = [k for k in self._matrix_cache if k.startswith(f"{repo_id}:")]
        for k in keys_to_remove:
            del self._matrix_cache[k]
        cache_service.invalidate_repo(repo_id)

        self.attached_repo_id = repo_id
        reused_count = len(chunks) - len(chunks_to_embed)
        logger.info(
            "Indexed %s chunks for repo %s (%s reused via cache/staging, %s newly embedded, gen=%d) using %s (dim=%d)",
            len(chunks), repo_id, reused_count, len(chunks_to_embed), next_gen, settings.EMBEDDING_MODEL, dimension
        )
        return len(chunks)

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete_repo(self, repo_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM project_index WHERE repo_id = ?", (repo_id,))
            conn.execute("DELETE FROM embedding_staging WHERE repo_id = ?", (repo_id,))
            if self._fts5_available:
                try:
                    conn.execute("INSERT INTO project_fts(project_fts) VALUES('rebuild')")
                except sqlite3.OperationalError:
                    pass
        if self.attached_repo_id == repo_id:
            self.attached_repo_id = None
        # Invalidate matrix and retrieval caches
        keys_to_remove = [k for k in self._matrix_cache if k.startswith(f"{repo_id}:")]
        for k in keys_to_remove:
            del self._matrix_cache[k]
        cache_service.invalidate_repo(repo_id)


vector_store = VectorStore()
