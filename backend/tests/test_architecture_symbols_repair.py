"""Tests for Architecture Symbol Schema Repair and Legacy Compatibility (Root Cause 1).

Validates:
1. Canonical empty symbol dictionary stored for non-indexable files (e.g. package-lock.json).
2. Non-indexable files retained in file tree / metadata without emitting RAG chunks.
3. _normalize_symbols handles None, [], "bad string", malformed nested items, and missing keys.
4. _build_codebase_graph accepts legacy projects containing symbols: [] without raising AttributeError.
5. Valid symbols and imports from neighboring files are preserved in graph nodes and edges.
6. All four diagram types (component_tree, api_flow, data_flow, db_schema) execute cleanly.
7. API regression: POST /api/architecture on legacy project with symbols=[] returns 200 OK without unhandled 500.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.services.architecture_service import ArchitectureService, architecture_service
from backend.services.ingestion_service import IngestionService, empty_symbol_dict
from backend.services.project_service import project_service


# ---------------------------------------------------------------------------
# 1 & 2: Ingestion Canonical Symbol Schema and RAG Chunk Exclusion
# ---------------------------------------------------------------------------
def test_non_indexable_file_stores_canonical_empty_symbols_and_no_rag_chunks():
    """Ingestion stores canonical empty symbol dict for non-indexable files like package-lock.json."""
    temp_dir = Path(tempfile.mkdtemp(prefix="repotalk_test_sym_"))
    try:
        (temp_dir / "package.json").write_text('{"name": "demo", "version": "1.0"}', encoding="utf-8")
        (temp_dir / "package-lock.json").write_text(
            '{"name": "demo", "lockfileVersion": 3, "packages": {}}', encoding="utf-8"
        )
        (temp_dir / "app.py").write_text(
            "import os\n\ndef run():\n    pass\n\nclass Server:\n    pass\n", encoding="utf-8"
        )

        svc = IngestionService()
        summary = svc.analyze_repository(temp_dir, "test_repo_sym", "DemoRepo")

        files_by_path = {f["path"]: f for f in summary["files"]}

        # package-lock.json must be present in files
        assert "package-lock.json" in files_by_path
        lock_meta = files_by_path["package-lock.json"]
        assert lock_meta["indexed"] is False
        assert lock_meta["index_skip_reason"] == "generated_dependency_file"

        # Canonical empty symbol dictionary
        expected_empty = {"functions": [], "classes": [], "imports": []}
        assert lock_meta["symbols"] == expected_empty
        assert isinstance(lock_meta["symbols"], dict)

        # Ensure mutable dictionary is not shared across entries
        empty_1 = empty_symbol_dict()
        empty_2 = empty_symbol_dict()
        assert empty_1 == empty_2
        assert empty_1 is not empty_2

        # RAG chunks check: package-lock.json must NOT produce any chunks
        chunk_files = {c["file_path"] for c in summary["chunks"]}
        assert "package-lock.json" not in chunk_files
        assert "app.py" in chunk_files
        assert "package.json" in chunk_files

        # Indexed file has extracted symbols
        app_meta = files_by_path["app.py"]
        assert app_meta["indexed"] is True
        assert "run" in [fn["name"] for fn in app_meta["symbols"]["functions"]]
        assert "Server" in [cl["name"] for cl in app_meta["symbols"]["classes"]]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3 & 4: Normalizer and Graph Builder Resilience to Legacy & Corrupt Metadata
# ---------------------------------------------------------------------------
def test_normalize_symbols_handles_legacy_and_corrupt_types():
    """_normalize_symbols handles None, [], numbers, bad strings, and malformed lists."""
    # Legacy empty list
    norm = ArchitectureService._normalize_symbols([])
    assert norm == {"functions": [], "classes": [], "imports": []}

    # None / null
    norm = ArchitectureService._normalize_symbols(None)
    assert norm == {"functions": [], "classes": [], "imports": []}

    # Arbitrary non-dict string or int
    norm = ArchitectureService._normalize_symbols("malformed_symbols_string")
    assert norm == {"functions": [], "classes": [], "imports": []}

    norm = ArchitectureService._normalize_symbols(12345)
    assert norm == {"functions": [], "classes": [], "imports": []}

    # Dict with invalid internal types
    corrupt_dict = {
        "functions": "not_a_list",
        "classes": None,
        "imports": 42,
    }
    norm = ArchitectureService._normalize_symbols(corrupt_dict)
    assert norm == {"functions": [], "classes": [], "imports": []}

    # Dict with malformed items inside lists
    mixed_dict = {
        "functions": [{"name": "valid_fn", "line": 10}, "bad_item", {"no_name": 1}, {"name": ""}, None],
        "classes": [{"name": "ValidClass"}, {"name": 123}, {}, None],
        "imports": ["os", "", 123, None, "sys"],
    }
    norm = ArchitectureService._normalize_symbols(mixed_dict)
    assert norm["functions"] == [{"name": "valid_fn", "line": 10}]
    assert norm["classes"] == [{"name": "ValidClass"}]
    assert norm["imports"] == ["os", "sys"]


# ---------------------------------------------------------------------------
# 5 & 6: Graph Construction for All 4 Diagram Types with Legacy Projects
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("diagram_type", ["component_tree", "api_flow", "data_flow", "db_schema"])
def test_build_codebase_graph_all_modes_with_legacy_metadata(diagram_type):
    """_build_codebase_graph builds nodes and edges from legacy metadata with symbols=[] without raising."""
    legacy_files = [
        {
            "path": "package-lock.json",
            "symbols": [],  # Legacy schema failure path
        },
        {
            "path": "corrupt_meta.py",
            "symbols": "invalid_string",
        },
        {
            "path": "null_meta.py",
            "symbols": None,
        },
        {
            "path": "missing_symbols.py",
            # No 'symbols' key at all
        },
        {
            "path": "src/controllers/api.py",
            "symbols": {
                "functions": [{"name": "get_users", "line": 12}],
                "classes": [{"name": "UserController", "line": 5}],
                "imports": ["models.user", "services.auth"],
            },
        },
        {
            "path": "src/models/user.py",
            "symbols": {
                "functions": [],
                "classes": [{"name": "UserModel", "line": 3}],
                "imports": ["db.connection"],
            },
        },
        {
            "path": "src/services/auth.py",
            "symbols": {
                "functions": [{"name": "authenticate_user", "line": 8}],
                "classes": [],
                "imports": ["models.user"],
            },
        },
    ]

    service = ArchitectureService()
    graph = service._build_codebase_graph(legacy_files, diagram_type)

    assert "nodes" in graph
    assert "edges" in graph
    assert isinstance(graph["nodes"], dict)
    assert isinstance(graph["edges"], list)

    # Valid nodes should be present
    nodes_by_file = {n["file"]: n for n in graph["nodes"].values()}
    assert "src/controllers/api.py" in nodes_by_file
    assert "src/models/user.py" in nodes_by_file
    assert "src/services/auth.py" in nodes_by_file

    # The malformed/legacy files must not crash graph building and can be represented
    assert "package-lock.json" in nodes_by_file

    api_node = nodes_by_file["src/controllers/api.py"]
    # Check that valid symbols are populated in node metadata
    assert "get_users" in api_node["description"]
    assert "UserController" in api_node["description"]

    # Verify import edges were resolved between valid files
    edge_pairs = [(e["source"], e["target"]) for e in graph["edges"]]
    assert ("src_controllers_api_py", "src_models_user_py") in edge_pairs or \
           ("src_controllers_api_py", "src_services_auth_py") in edge_pairs or \
           ("src_services_auth_py", "src_models_user_py") in edge_pairs


# ---------------------------------------------------------------------------
# 7: API Route Regression: POST /api/architecture with Legacy Project on Disk
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_architecture_legacy_project_regression(tmp_path):
    """POST /api/architecture does not fail with 500 when project metadata has symbols=[]."""
    test_session = "test_session_legacy_arch"
    repo_id = "repo_legacy_arch_123"

    # Set up mock session and project structure in isolated DATA_DIR
    with patch.object(settings, "DATA_DIR", tmp_path):
        legacy_project = {
            "repo_id": repo_id,
            "name": "LegacyRepo",
            "source_type": "zip",
            "url_or_name": "LegacyRepo",
            "created_at": "2026-09-17 12:00:00",
            "file_count": 2,
            "total_lines": 10,
            "stack": ["JavaScript"],
            "files": [
                {
                    "path": "package-lock.json",
                    "symbols": [],  # The exact production failure trigger
                    "indexed": False,
                    "index_skip_reason": "generated_dependency_file",
                },
                {
                    "path": "server.js",
                    "symbols": {
                        "functions": [{"name": "startServer", "line": 1}],
                        "classes": [],
                        "imports": ["express"],
                    },
                    "indexed": True,
                },
            ],
            "tree": [
                {"name": "package-lock.json", "type": "file"},
                {"name": "server.js", "type": "file"},
            ],
            "root_path": str(tmp_path),
        }

        project_service._save_data(test_session, {
            "active_repo_id": repo_id,
            "projects": {repo_id: legacy_project},
        })

        mock_gemini_response = json.dumps({
            "diagram_type": "component_tree",
            "has_data": True,
            "nodes": {
                "server_js": {
                    "label": "Server",
                    "type": "Backend",
                    "file": "server.js",
                    "dir": "root",
                    "description": "Express server",
                    "connected_to": [],
                    "data_passed": [],
                }
            },
            "edges": [],
        })

        client = TestClient(app)
        with patch("backend.services.vector_store.vector_store.search", new_callable=AsyncMock) as mock_search, \
             patch("backend.services.gemini_service.gemini_service.generate_text", new_callable=AsyncMock) as mock_gen:

            mock_search.return_value = []
            mock_gen.return_value = mock_gemini_response

            response = client.post(
                "/api/architecture",
                json={"repo_id": repo_id, "diagram_type": "component_tree"},
                headers={"X-Session-Id": test_session},
            )

            assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}: {response.text}"
            data = response.json()
            assert data["diagram_type"] == "component_tree"
            assert "nodes" in data
            assert "edges" in data
