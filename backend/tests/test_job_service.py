"""Unit tests for JobService durable SQLite job queue."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.config import settings
from backend.services.job_service import JobService


@pytest.fixture
def temp_job_service(tmp_path):
    with patch.object(settings, "DATA_DIR", tmp_path):
        service = JobService()
        yield service


def test_create_and_get_job(temp_job_service):
    """Creating a job persists it to SQLite and returns job record."""
    job = temp_job_service.create_job("repo_123", "sess_abc", "github", "https://github.com/user/repo")
    job_id = job["job_id"]
    assert job_id.startswith("job_")

    fetched = temp_job_service.get_job(job_id)
    assert fetched is not None
    assert fetched["job_id"] == job_id
    assert fetched["repo_id"] == "repo_123"
    assert fetched["status"] == "queued"
    assert fetched["stage"] == "queued"


def test_update_progress_and_completion(temp_job_service):
    """Progress updates modify stage and progress percentage."""
    job = temp_job_service.create_job("repo_progress", "sess_1", "upload", "archive.zip")
    job_id = job["job_id"]
    temp_job_service.update_progress(job_id, status="running", stage="embedding", progress_percent=50)

    fetched = temp_job_service.get_job(job_id)
    assert fetched["stage"] == "embedding"
    assert fetched["progress_percent"] == 50

    temp_job_service.update_progress(job_id, status="completed", stage="completed", progress_percent=100)
    job_done = temp_job_service.get_job(job_id)
    assert job_done["status"] == "completed"
    assert job_done["progress_percent"] == 100


def test_cancel_job(temp_job_service):
    """Canceling a job sets status to CANCELLED and marks cancellation event."""
    job = temp_job_service.create_job("repo_cancel", "sess_2", "upload", "archive.zip")
    job_id = job["job_id"]
    assert not temp_job_service.is_cancelled(job_id)

    success = temp_job_service.cancel_job(job_id)
    assert success is True
    assert temp_job_service.is_cancelled(job_id)

    fetched = temp_job_service.get_job(job_id)
    assert fetched["status"] == "cancelled"


def test_fail_job(temp_job_service):
    """Failing a job records the error string."""
    job = temp_job_service.create_job("repo_fail", "sess_3", "upload", "archive.zip")
    job_id = job["job_id"]
    temp_job_service.update_progress(job_id, status="failed", stage="failed", error_message="Clone failed: repository not found")

    fetched = temp_job_service.get_job(job_id)
    assert fetched["status"] == "failed"
    assert "not found" in fetched["error_message"]


def test_startup_recovery(tmp_path):
    """Active jobs at shutdown are marked retryable on restart."""
    with patch.object(settings, "DATA_DIR", tmp_path):
        service1 = JobService()
        job = service1.create_job("repo_restart", "sess_4", "upload", "repo.zip")
        job_id = job["job_id"]
        service1.update_progress(job_id, status="running", stage="analyzing", progress_percent=30)

        # Simulate service restart
        service2 = JobService()
        recovered = service2.get_job(job_id)
        assert recovered["status"] == "interrupted"
        assert "interrupted by a server restart" in recovered["error_message"]


def test_job_authorization_and_session_isolation(temp_job_service):
    """Session B cannot read or cancel Session A's job."""
    job = temp_job_service.create_job("repo_sec", "session_a", "github", "https://github.com/a/repo")
    job_id = job["job_id"]

    # Session A can read
    job_a = temp_job_service.get_job(job_id, session_id="session_a")
    assert job_a is not None
    assert job_a["job_id"] == job_id

    # Session B cannot read (returns None)
    job_b = temp_job_service.get_job(job_id, session_id="session_b")
    assert job_b is None

    # Session B cannot cancel (returns False)
    assert temp_job_service.cancel_job(job_id, session_id="session_b") is False
    assert not temp_job_service.is_cancelled(job_id)

    # Session A can cancel (returns True)
    assert temp_job_service.cancel_job(job_id, session_id="session_a") is True
    assert temp_job_service.is_cancelled(job_id)


def test_public_job_shape_hides_session_and_lease(temp_job_service):
    """Public job responses must never leak session_id or worker_lease."""
    job = temp_job_service.create_job("repo_priv", "session_secret_123", "github", "https://github.com/priv/repo")
    job_id = job["job_id"]

    # Returned on creation
    assert "session_id" not in job
    assert "worker_lease" not in job

    # Returned on get_job(..., public=True)
    pub = temp_job_service.get_job(job_id, session_id="session_secret_123", public=True)
    assert pub is not None
    assert "session_id" not in pub
    assert "worker_lease" not in pub
    assert "status" in pub
    assert "job_id" in pub

    # Internal get_job(..., public=False) retains session_id for backend use
    internal = temp_job_service.get_job(job_id, public=False)
    assert internal["session_id"] == "session_secret_123"


def test_terminal_state_cannot_be_overwritten(temp_job_service):
    """Once a job is cancelled or completed, late updates cannot resurrect it."""
    job = temp_job_service.create_job("repo_terminal", "sess_term", "upload", "archive.zip")
    job_id = job["job_id"]

    temp_job_service.cancel_job(job_id, session_id="sess_term")
    assert temp_job_service.get_job(job_id)["status"] == "cancelled"

    # Simulate late worker calling update_progress with completed
    temp_job_service.update_progress(job_id, status="completed", stage="completed", progress_percent=100)

    # Must remain cancelled!
    res = temp_job_service.get_job(job_id)
    assert res["status"] == "cancelled"
    assert res["progress_percent"] == 0


@pytest.mark.asyncio
async def test_sse_subscribe_terminal_immediate_done(temp_job_service):
    """Subscribing to an already completed/cancelled job terminates immediately without hanging."""
    job = temp_job_service.create_job("repo_sse", "sess_sse", "upload", "archive.zip")
    job_id = job["job_id"]
    temp_job_service.update_progress(job_id, status="completed", stage="completed", progress_percent=100)

    frames = []
    async for frame in temp_job_service.subscribe(job_id, session_id="sess_sse"):
        frames.append(frame)

    assert len(frames) == 2
    assert "completed" in frames[0]
    assert "session_id" not in frames[0]
    assert frames[1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_sse_subscribe_unauthorized_yields_empty(temp_job_service):
    """Subscribing to another session's job yields nothing and returns immediately."""
    job = temp_job_service.create_job("repo_sse_unauth", "sess_owner", "upload", "archive.zip")
    job_id = job["job_id"]

    frames = []
    async for frame in temp_job_service.subscribe(job_id, session_id="sess_attacker"):
        frames.append(frame)

    assert len(frames) == 0

