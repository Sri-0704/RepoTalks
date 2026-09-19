"""Durable SQLite Ingestion Job Service for RepoTalk.

Handles background ingestion jobs with:
1. SQLite persistence across server restarts (jobs.db)
2. State machine: queued -> cloning/extracting -> analyzing -> embedding -> indexing -> completed / failed / cancelled / interrupted / paused_for_key
3. Worker lease and restart recovery (marks interrupted jobs as retryable)
4. Ephemeral in-memory key storage (user keys are never written to disk)
5. SSE progress streaming via asyncio queues
6. Concurrency limiting (1 active ingestion job at a time to preserve interactive headroom)
"""

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Iterator, List, Optional

from backend.config import settings

logger = logging.getLogger(__name__)


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}

PUBLIC_JOB_KEYS = {
    "job_id",
    "repo_id",
    "source_type",
    "url_or_name",
    "status",
    "stage",
    "progress_percent",
    "total_files",
    "processed_files",
    "total_chunks",
    "embedded_chunks",
    "error_message",
    "created_at",
    "updated_at",
    "current_batch",
    "total_batches",
    "retry_attempt",
    "retry_delay_seconds",
    "error_code",
}


def to_public_job(data: Dict[str, Any]) -> Dict[str, Any]:
    """Strip session_id, worker_lease, and any internal fields."""
    return {k: v for k, v in data.items() if k in PUBLIC_JOB_KEYS}


class JobService:
    def __init__(self):
        self.db_path = settings.DATA_DIR / "db" / "jobs.db"
        self._init_db()
        # Ephemeral in-memory store for API keys (never saved to SQLite)
        self._key_store: Dict[str, str] = {}
        # SSE subscriber queues per job_id
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # Cancellation events per job_id
        self._cancel_events: Dict[str, asyncio.Event] = {}
        # Limit to 1 active ingestion job at a time
        self._concurrency_semaphore = asyncio.Semaphore(1)
        # Recover interrupted jobs on startup
        self.recover_interrupted_jobs()

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
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    job_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    url_or_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    total_chunks INTEGER NOT NULL DEFAULT 0,
                    embedded_chunks INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    worker_lease TEXT
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_status ON ingestion_jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_session ON ingestion_jobs(session_id)")

    def recover_interrupted_jobs(self) -> int:
        """Mark any jobs left running/queued across restart as interrupted/retryable."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'interrupted',
                    stage = 'interrupted',
                    error_message = 'Ingestion was interrupted by a server restart. It can be retried.',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('running', 'queued')
                """
            )
            count = cursor.rowcount
            if count > 0:
                logger.info("Marked %d interrupted ingestion job(s) from previous run as retryable", count)
            return count

    def create_job(
        self,
        repo_id: str,
        session_id: str,
        source_type: str,
        url_or_name: str,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        job_id = f"job_{uuid.uuid4().hex}"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_jobs (
                    job_id, repo_id, session_id, source_type, url_or_name,
                    status, stage, progress_percent, total_files, processed_files,
                    total_chunks, embedded_chunks, worker_lease
                ) VALUES (?, ?, ?, ?, ?, 'queued', 'queued', 0, 0, 0, 0, 0, ?)
                """,
                (job_id, repo_id, session_id, source_type, url_or_name, "worker-main"),
            )

        if api_key:
            self._key_store[job_id] = api_key
        self._cancel_events[job_id] = asyncio.Event()

        job = self.get_job(job_id, public=True)
        logger.info("Created ingestion job %s for repo %s (%s)", job_id, repo_id, source_type)
        return job or {}

    def get_job(
        self,
        job_id: str,
        session_id: Optional[str] = None,
        public: bool = True,
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            job_dict = dict(row)
            if session_id is not None and job_dict.get("session_id") != session_id:
                return None
            return to_public_job(job_dict) if public else job_dict

    def list_jobs(
        self,
        session_id: Optional[str] = None,
        limit: int = 20,
        public: bool = True,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM ingestion_jobs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [to_public_job(dict(r)) if public else dict(r) for r in rows]

    def update_progress(
        self,
        job_id: str,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress_percent: Optional[int] = None,
        total_files: Optional[int] = None,
        processed_files: Optional[int] = None,
        total_chunks: Optional[int] = None,
        embedded_chunks: Optional[int] = None,
        error_message: Optional[str] = None,
        current_batch: Optional[int] = None,
        total_batches: Optional[int] = None,
        retry_attempt: Optional[int] = None,
        retry_delay_seconds: Optional[float] = None,
        error_code: Optional[str] = None,
    ) -> None:
        # Enforce terminal state guard: cannot overwrite a terminal state
        current = self.get_job(job_id, public=False)
        if not current:
            return
        if current.get("status") in TERMINAL_STATUSES:
            logger.warning(
                "Refused to update job %s: already in terminal status '%s'",
                job_id,
                current.get("status"),
            )
            return

        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if stage is not None:
            updates.append("stage = ?")
            params.append(stage)
        if progress_percent is not None:
            updates.append("progress_percent = ?")
            params.append(progress_percent)
        if total_files is not None:
            updates.append("total_files = ?")
            params.append(total_files)
        if processed_files is not None:
            updates.append("processed_files = ?")
            params.append(processed_files)
        if total_chunks is not None:
            updates.append("total_chunks = ?")
            params.append(total_chunks)
        if embedded_chunks is not None:
            updates.append("embedded_chunks = ?")
            params.append(embedded_chunks)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        params.append(job_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE ingestion_jobs SET {', '.join(updates)} WHERE job_id = ?",
                params,
            )

        # Notify SSE subscribers with structured public progress
        job_data = self.get_job(job_id, public=True)
        if job_data:
            # Augment with ephemeral retry/batch state (not persisted to DB)
            if current_batch is not None:
                job_data["current_batch"] = current_batch
            if total_batches is not None:
                job_data["total_batches"] = total_batches
            if retry_attempt is not None:
                job_data["retry_attempt"] = retry_attempt
            if retry_delay_seconds is not None:
                job_data["retry_delay_seconds"] = retry_delay_seconds
            if error_code is not None:
                job_data["error_code"] = error_code
            self._notify_subscribers(job_id, job_data)

    def cancel_job(self, job_id: str, session_id: Optional[str] = None) -> bool:
        job = self.get_job(job_id, session_id=session_id, public=False)
        if not job:
            return False
        if job.get("status") in TERMINAL_STATUSES:
            return job.get("status") == "cancelled"

        if job_id in self._cancel_events:
            self._cancel_events[job_id].set()
        self.update_progress(
            job_id,
            status="cancelled",
            stage="cancelled",
            error_message="Job was cancelled by the user.",
        )
        self._key_store.pop(job_id, None)
        return True

    def is_cancelled(self, job_id: str) -> bool:
        event = self._cancel_events.get(job_id)
        return event.is_set() if event else False

    def get_job_key(self, job_id: str) -> Optional[str]:
        return self._key_store.get(job_id)

    def attach_key(self, job_id: str, api_key: str) -> bool:
        """Re-attach key for an interrupted or paused job without storing to disk."""
        job = self.get_job(job_id, public=False)
        if not job:
            return False
        self._key_store[job_id] = api_key
        if job.get("status") == "paused_for_key":
            self.update_progress(job_id, status="queued", stage="queued", error_message=None)
        return True

    # ------------------------------------------------------------------
    # SSE Broadcasting
    # ------------------------------------------------------------------

    def _notify_subscribers(self, job_id: str, data: Dict[str, Any]) -> None:
        if job_id in self._subscribers:
            pub_data = to_public_job(data)
            for q in list(self._subscribers[job_id]):
                try:
                    q.put_nowait(pub_data)
                except Exception:
                    pass

    async def subscribe(
        self, job_id: str, session_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        # Authorize against session and ensure job exists
        initial = self.get_job(job_id, session_id=session_id, public=True)
        if not initial:
            return

        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        if job_id not in self._subscribers:
            self._subscribers[job_id] = []
        self._subscribers[job_id].append(q)

        # Send initial state
        yield f"data: {json.dumps(initial)}\n\n"
        if initial.get("status") in TERMINAL_STATUSES:
            yield "data: [DONE]\n\n"
            if job_id in self._subscribers and q in self._subscribers[job_id]:
                self._subscribers[job_id].remove(q)
                if not self._subscribers[job_id]:
                    del self._subscribers[job_id]
            return

        try:
            while True:
                data = await q.get()
                pub_data = to_public_job(data)
                yield f"data: {json.dumps(pub_data)}\n\n"
                if pub_data.get("status") in TERMINAL_STATUSES:
                    yield "data: [DONE]\n\n"
                    break
        finally:
            if job_id in self._subscribers and q in self._subscribers[job_id]:
                self._subscribers[job_id].remove(q)
                if not self._subscribers[job_id]:
                    del self._subscribers[job_id]


job_service = JobService()
