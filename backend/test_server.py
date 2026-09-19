"""Regression coverage for repository isolation and import boundaries.

These tests never contact Gemini or clone a remote repository. They replace
embeddings with deterministic fixtures so that storage behavior is testable.
"""
import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.services.gemini_service import GeminiServiceError, gemini_service
from backend.services.ingestion_service import ingestion_service
from backend.services.vector_store import vector_store

client = TestClient(app)


async def deterministic_embeddings(texts, override_key=None, **kwargs):
    return [[float(len(text) % 7), float(sum(map(ord, text)) % 11), 1.0] for text in texts]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    monkeypatch.setattr(gemini_service, "get_embeddings", deterministic_embeddings)


def archive(files: dict[str, str]) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zip_file:
        for name, content in files.items():
            zip_file.writestr(name, content)
    return data.getvalue()


def upload(session: str, filename: str, files: dict[str, str]):
    return client.post(
        "/api/ingest/upload",
        headers={"X-Session-Id": session},
        files={"file": (filename, archive(files), "application/zip")},
    )


def delete_project(session: str, repo_id: str):
    return client.delete(f"/api/projects/{repo_id}", headers={"X-Session-Id": session})


def test_same_filename_imports_are_isolated_and_unauthorized_delete_is_harmless():
    first = upload("owner-a", "project.zip", {"app.py": "OWNER_A = True"})
    second = upload("owner-b", "project.zip", {"app.py": "OWNER_B = True"})
    assert first.status_code == second.status_code == 200
    first_project, second_project = first.json()["project"], second.json()["project"]
    assert first_project["repo_id"] != second_project["repo_id"]
    assert "OWNER_A" in (Path(first_project["root_path"]) / "app.py").read_text()
    assert "OWNER_B" in (Path(second_project["root_path"]) / "app.py").read_text()

    count_before = vector_store.get_chunk_count(first_project["repo_id"])
    denied = delete_project("owner-b", first_project["repo_id"])
    assert denied.status_code == 404
    assert vector_store.get_chunk_count(first_project["repo_id"]) == count_before

    assert delete_project("owner-a", first_project["repo_id"]).status_code == 200
    assert delete_project("owner-b", second_project["repo_id"]).status_code == 200


def test_upload_filename_cannot_escape_and_upload_limit_is_enforced(monkeypatch):
    marker = settings.DATA_DIR / "upload-marker.zip"
    marker.write_text("unchanged")
    response = upload("owner-a", "../upload-marker.zip", {"app.py": "safe = True"})
    assert response.status_code == 200
    assert marker.read_text() == "unchanged"
    assert delete_project("owner-a", response.json()["project"]["repo_id"]).status_code == 200

    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0)
    too_large = upload("owner-a", "project.zip", {"app.py": "x = 1"})
    assert too_large.status_code == 413
    marker.unlink()


def test_unsafe_zip_members_are_rejected_without_writing_outside_destination():
    response = upload("owner-a", "project.zip", {"../../outside.py": "not allowed"})
    assert response.status_code == 400
    assert not (settings.DATA_DIR.parent / "outside.py").exists()


def test_static_route_rejects_parent_directory_paths():
    response = client.get("/%2e%2e/AUDIT_REPORT.md")
    assert response.status_code == 404


def test_tree_keeps_private_files_and_normalizes_windows_paths():
    tree = ingestion_service.build_file_tree(["__init__.py", "_helper.py", r"backend\services\worker.py"])
    names = {node["name"] for node in tree}
    assert "__init__.py" in names
    assert "_helper.py" in names
    backend = next(node for node in tree if node["name"] == "backend")
    assert backend["children"][0]["name"] == "services"


def test_exact_file_retrieval_respects_top_k_and_chunk_order():
    repo_id = ingestion_service.new_repo_id()
    chunks = [
        {"chunk_id": f"large.py_chunk_{index}", "chunk_order": index, "file_path": "large.py", "chunk_text": f"line {index}", "metadata": {}}
        for index in range(13)
    ]
    asyncio.run(vector_store.add_chunks(repo_id, chunks))
    results = asyncio.run(vector_store.search(repo_id, "explain large.py", top_k=1))
    assert len(results) == 1
    assert results[0]["chunk_id"] == "large.py_chunk_0"
    vector_store.delete_repo(repo_id)


def test_provider_failure_is_reported_instead_of_fabricating_a_viva_score(monkeypatch):
    repo_id = ingestion_service.new_repo_id()
    project_root = settings.DATA_DIR / "repos" / repo_id
    project_root.mkdir(parents=True)
    project_root.joinpath("app.py").write_text("def add(a, b): return a + b")
    summary = ingestion_service.analyze_repository(project_root, repo_id, "calculator")
    from backend.services.project_service import project_service
    project_service.register_project("owner-a", summary)
    asyncio.run(vector_store.add_chunks(repo_id, summary["chunks"]))

    async def unavailable(*args, **kwargs):
        raise GeminiServiceError("Gemini unavailable")

    monkeypatch.setattr(gemini_service, "generate_text", unavailable)
    response = client.post(
        "/api/viva/evaluate",
        headers={"X-Session-Id": "owner-a"},
        json={"repo_id": repo_id, "question": "What does add do?", "student_answer": "I do not know."},
    )
    assert response.status_code == 503
    assert "score" not in response.json()
    delete_project("owner-a", repo_id)


def test_job_authorization_and_session_isolation_http():
    """HTTP endpoints /api/jobs/{job_id} and /api/jobs/{job_id}/cancel enforce session ownership."""
    from backend.services.job_service import job_service

    job = job_service.create_job("repo_http_test", "owner-a", "github", "https://github.com/a/repo")
    job_id = job["job_id"]

    # Owner A can read job details and receives public shape
    res_a = client.get(f"/api/jobs/{job_id}", headers={"X-Session-Id": "owner-a"})
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["job_id"] == job_id
    assert "session_id" not in data_a
    assert "worker_lease" not in data_a

    # Owner B receives 404 (non-disclosing)
    res_b = client.get(f"/api/jobs/{job_id}", headers={"X-Session-Id": "owner-b"})
    assert res_b.status_code == 404

    # Owner B cannot cancel Owner A's job (receives 404)
    cancel_b = client.post(f"/api/jobs/{job_id}/cancel", headers={"X-Session-Id": "owner-b"})
    assert cancel_b.status_code == 404
    assert not job_service.is_cancelled(job_id)

    # Owner A can cancel job
    cancel_a = client.post(f"/api/jobs/{job_id}/cancel", headers={"X-Session-Id": "owner-a"})
    assert cancel_a.status_code == 200
    assert cancel_a.json() == {"success": True}
    assert job_service.is_cancelled(job_id)


def test_health_check_readiness():
    """Verify /api/health returns 200 with db and disk checks."""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert data["db"] == "ok"
    assert data["disk"] == "ok"


def test_session_cookie_secure_in_https():
    """Verify session cookie sets secure=True and samesite=none when HTTPS is detected."""
    res = client.get("/api/projects", headers={"X-Forwarded-Proto": "https"})
    assert res.status_code == 200
    cookie_header = res.headers.get("set-cookie", "")
    assert "session_id=" in cookie_header
    assert "Secure" in cookie_header
    assert "samesite=none" in cookie_header.lower()


def test_vercel_cors_origin():
    """Verify that Vercel origins match CORS_ORIGIN_REGEX and receive allow-origin."""
    res = client.options(
        "/api/health",
        headers={
            "Origin": "https://repotalks-demo.vercel.app",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "https://repotalks-demo.vercel.app"


