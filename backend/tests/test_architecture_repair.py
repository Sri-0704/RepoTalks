"""Tests for Architecture Mode Separation, Strict Validation, Truthful Failure (no base_graph swallow),
and Isolated Caching (Root Cause 1 & 2 repair).

Validates:
1. DiagramType validation rejects invalid diagram modes with 422 Unprocessable Entity.
2. Provider failures (e.g. GeminiModelUnavailableError, 503, rate limits) are returned honestly as HTTP 503,
   NEVER swallowed into a 200 with base_graph.
3. Node/edge separation: api_flow, data_flow, and db_schema are isolated to mode-specific nodes
   and do NOT dump the entire file-tree directory hierarchy into the graph.
4. Versioned cache (v2) correctly isolates by diagram_type and model.
5. force_refresh bypasses cache and writes fresh results.
6. DB schema detection returns has_data=False when repository has no database models.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.services.architecture_service import architecture_service, ArchitectureResponseModel
from backend.services.cache_service import cache_service
from backend.services.project_service import project_service
from backend.services.gemini_service import GeminiModelUnavailableError, GeminiServiceError


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_project(tmp_path):
    """Register a temporary test project for session 'test-session'."""
    repo_id = "repo_arch_test_123"
    session_id = "test-session"
    files = [
        {"path": "backend/main.py", "extension": ".py", "line_count": 100, "symbols": {"functions": [{"name": "app"}], "classes": [], "imports": ["fastapi"]}},
        {"path": "backend/services/ingestion.py", "extension": ".py", "line_count": 80, "symbols": {"functions": [{"name": "ingest"}], "classes": [], "imports": []}},
        {"path": "frontend/app/page.tsx", "extension": ".tsx", "line_count": 50, "symbols": {"functions": [], "classes": [], "imports": []}},
        {"path": "frontend/lib/api.ts", "extension": ".ts", "line_count": 60, "symbols": {"functions": [{"name": "fetchApi"}], "classes": [], "imports": []}},
    ]
    meta = {
        "repo_id": repo_id,
        "name": "arch-test-repo",
        "session_id": session_id,
        "root_path": str(tmp_path),
        "files": files,
        "indexed": True,
        "index_status": "ready",
    }
    project_service._save_data(session_id, {
        "active_repo_id": repo_id,
        "projects": {repo_id: meta},
    })
    yield repo_id, session_id, files
    # Cleanup
    try:
        project_service.delete_project(session_id, repo_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. Invalid diagram_type returns 422
# ---------------------------------------------------------------------------
def test_invalid_diagram_type_returns_422(client, test_project):
    repo_id, session_id, _ = test_project
    res = client.post(
        "/api/architecture",
        headers={"X-Session-Id": session_id},
        json={"repo_id": repo_id, "diagram_type": "invalid_mode"}
    )
    assert res.status_code == 422
    err_detail = res.json()["detail"]
    assert any("diagram_type" in str(e) for e in err_detail)


# ---------------------------------------------------------------------------
# 2. Provider failure returns HTTP 503 and is NEVER swallowed into HTTP 200 base_graph
# ---------------------------------------------------------------------------
def test_provider_failure_returns_503_not_swallowed(client, test_project):
    repo_id, session_id, _ = test_project

    with patch("backend.services.gemini_service.gemini_service.generate_text", side_effect=GeminiModelUnavailableError("Model 'gemini-3.6-flash' is unavailable")):
        res = client.post(
            "/api/architecture",
            headers={"X-Session-Id": session_id},
            json={"repo_id": repo_id, "diagram_type": "api_flow", "force_refresh": True}
        )

    assert res.status_code == 503
    payload = res.json()
    assert "unavailable" in payload["detail"].lower()
    assert payload.get("error_code") == "GeminiModelUnavailableError"


# ---------------------------------------------------------------------------
# 3. Architecture modes are semantically isolated (no generic file tree in api_flow)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_architecture_modes_semantic_isolation(test_project):
    repo_id, session_id, files = test_project

    # Mock LLM returning api_flow specific nodes
    llm_api_flow_json = json.dumps({
        "diagram_type": "api_flow",
        "has_data": True,
        "nodes": {
            "api_endpoint": {
                "label": "POST /api/chat",
                "type": "API Endpoint",
                "file": "backend/main.py",
                "dir": "backend",
                "description": "Streaming chat endpoint",
                "connected_to": ["service_handler"],
                "data_passed": ["message →"]
            },
            "service_handler": {
                "label": "IngestionService",
                "type": "Service Layer",
                "file": "backend/services/ingestion.py",
                "dir": "backend/services",
                "description": "Processes chat query",
                "connected_to": [],
                "data_passed": []
            }
        },
        "edges": [
            {"source": "api_endpoint", "target": "service_handler", "label": "message →"}
        ]
    })

    with patch("backend.services.gemini_service.gemini_service.generate_text", return_value=llm_api_flow_json), \
         patch("backend.services.vector_store.vector_store.search", return_value=[]):
        result = await architecture_service.generate_architecture_map(
            repo_id=repo_id,
            diagram_type="api_flow",
            force_refresh=True,
            session_id=session_id
        )

    # Validate schema
    validated = ArchitectureResponseModel.model_validate(result)
    assert validated.diagram_type == "api_flow"
    assert validated.has_data is True

    # Crucial assertion: api_flow MUST NOT contain generic directory container nodes
    node_ids = set(result["nodes"].keys())
    assert "api_endpoint" in node_ids
    assert "service_handler" in node_ids
    assert not any(nid.startswith("dir_") for nid in node_ids), "api_flow must not contain directory cluster nodes"
    assert "frontend_app_page_tsx" not in node_ids, "api_flow must not contain unrelated frontend file nodes"


# ---------------------------------------------------------------------------
# 4. Component tree contains codebase hierarchy
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_component_tree_mode_contains_hierarchy(test_project):
    repo_id, session_id, files = test_project

    llm_comp_json = json.dumps({
        "diagram_type": "component_tree",
        "has_data": True,
        "nodes": {
            "custom_layer": {
                "label": "Core Architecture",
                "type": "Layer",
                "description": "Application architecture",
                "connected_to": [],
            }
        },
        "edges": []
    })

    with patch("backend.services.gemini_service.gemini_service.generate_text", return_value=llm_comp_json), \
         patch("backend.services.vector_store.vector_store.search", return_value=[]):
        result = await architecture_service.generate_architecture_map(
            repo_id=repo_id,
            diagram_type="component_tree",
            force_refresh=True,
            session_id=session_id
        )

    node_ids = set(result["nodes"].keys())
    # In component_tree mode, directories and files are present
    assert any(nid.startswith("dir_") for nid in node_ids)
    assert "backend_main_py" in node_ids


# ---------------------------------------------------------------------------
# 5. DB schema returns has_data=False when repository lacks DB models
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_db_schema_no_database_files(test_project):
    repo_id, session_id, files = test_project

    result = await architecture_service.generate_architecture_map(
        repo_id=repo_id,
        diagram_type="db_schema",
        force_refresh=True,
        session_id=session_id
    )

    assert result["diagram_type"] == "db_schema"
    assert result["has_data"] is False
    assert "No database models" in result["reason"]
    assert result["nodes"] == {}
    assert result["edges"] == []


# ---------------------------------------------------------------------------
# 6. Force refresh bypasses cache
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(test_project):
    repo_id, session_id, files = test_project
    cache_key = f"v2:api_flow:{settings.DEFAULT_PRO_MODEL}"

    # Pre-populate cache with initial payload
    initial_payload = {
        "diagram_type": "api_flow",
        "has_data": True,
        "nodes": {"old_node": {"id": "old_node", "label": "Old Node", "type": "Old", "connected_to": []}},
        "edges": [],
        "mind_map": None
    }
    cache_service.set_artifact(repo_id, "architecture", cache_key, initial_payload)

    # Without force_refresh: returns cached
    res1 = await architecture_service.generate_architecture_map(
        repo_id=repo_id,
        diagram_type="api_flow",
        force_refresh=False,
        session_id=session_id
    )
    assert "old_node" in res1["nodes"]

    # With force_refresh: calls LLM and overwrites cache
    fresh_json = json.dumps({
        "diagram_type": "api_flow",
        "has_data": True,
        "nodes": {"fresh_node": {"label": "Fresh Node", "type": "Fresh", "connected_to": []}},
        "edges": []
    })
    with patch("backend.services.gemini_service.gemini_service.generate_text", return_value=fresh_json), \
         patch("backend.services.vector_store.vector_store.search", return_value=[]):
        res2 = await architecture_service.generate_architecture_map(
            repo_id=repo_id,
            diagram_type="api_flow",
            force_refresh=True,
            session_id=session_id
        )

    assert "fresh_node" in res2["nodes"]
    assert "old_node" not in res2["nodes"]


# ---------------------------------------------------------------------------
# 7. Reasoning models with <think> tags are stripped and parsed correctly
# ---------------------------------------------------------------------------
def test_parse_and_enrich_architecture_strips_think_tags():
    raw_response = """<think>
Let's analyze the codebase for API endpoints.
1. backend/main.py defines /api/architecture.
2. frontend calls fetchArchitectureMap.
Now I'll output the JSON.
</think>
```json
{
    "diagram_type": "api_flow",
    "has_data": true,
    "nodes": {
        "api_arch": {
            "label": "Architecture API",
            "type": "API Endpoint",
            "file": "backend/main.py",
            "connected_to": []
        }
    },
    "edges": []
}
```"""
    parsed = architecture_service._parse_and_enrich_architecture(
        response_text=raw_response,
        diagram_type="api_flow",
        base_graph={"nodes": {}, "edges": []}
    )
    assert parsed["diagram_type"] == "api_flow"
    assert parsed["has_data"] is True
    assert "api_arch" in parsed["nodes"]
    assert parsed["nodes"]["api_arch"]["label"] == "Architecture API"

