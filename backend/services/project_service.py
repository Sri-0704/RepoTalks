import os
import json
import re
import shutil
import hashlib
import logging
import threading
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from backend.config import settings

logger = logging.getLogger(__name__)

class ProjectService:
    def __init__(self):
        self._locks: Dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _get_lock(self, session_id: str) -> threading.RLock:
        clean_sid = self._sanitize_session_id(session_id)
        with self._locks_guard:
            return self._locks.setdefault(clean_sid, threading.RLock())

    def _sanitize_session_id(self, session_id: str) -> str:
        # Map opaque session credentials to a fixed safe path without reducing
        # distinct values such as "a/b" and "ab" to the same registry.
        return hashlib.sha256((session_id or "default").encode("utf-8")).hexdigest()

    def _get_session_dir(self, session_id: str) -> Path:
        clean_sid = self._sanitize_session_id(session_id)
        return settings.DATA_DIR / "sessions" / clean_sid

    def _get_session_file(self, session_id: str) -> Path:
        return self._get_session_dir(session_id) / "projects.json"

    def _load_data(self, session_id: str) -> Dict[str, Any]:
        session_file = self._get_session_file(session_id)
        try:
            if session_file.exists():
                return json.loads(session_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error reading projects file for session '{session_id}': {e}")
            raise RuntimeError("Project metadata is unreadable; refusing to discard it.") from e
        return {"active_repo_id": None, "projects": {}}

    def _save_data(self, session_id: str, data: Dict[str, Any]):
        session_dir = self._get_session_dir(session_id)
        session_file = self._get_session_file(session_id)
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            temporary_file = session_file.with_name(f".{session_file.name}.{uuid.uuid4().hex}.tmp")
            temporary_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temporary_file.replace(session_file)
        except Exception as e:
            logger.error(f"Error saving projects file for session '{session_id}': {e}")
            raise RuntimeError("Unable to save project metadata.") from e

    def detect_stack(self, root_dir: Path, files: List[Dict[str, Any]]) -> List[str]:
        stack = set()
        
        # Check extensions
        exts = {f.get("extension", "") for f in files}
        if ".py" in exts:
            stack.add("Python")
        if ".ts" in exts or ".tsx" in exts:
            stack.add("TypeScript")
        elif ".js" in exts or ".jsx" in exts:
            stack.add("JavaScript")
        if ".go" in exts:
            stack.add("Go")
        if ".rs" in exts:
            stack.add("Rust")
        if ".java" in exts:
            stack.add("Java")
        if ".sql" in exts:
            stack.add("SQL")
        if ".sh" in exts:
            stack.add("Bash")

        # Check package.json
        if root_dir.exists() and root_dir.is_dir():
            pkg_json_files = list(root_dir.rglob("package.json"))
            for pkg_path in pkg_json_files:
                if "node_modules" in str(pkg_path):
                    continue
                try:
                    content = json.loads(pkg_path.read_text(encoding="utf-8", errors="ignore"))
                    deps = {**content.get("dependencies", {}), **content.get("devDependencies", {})}
                    if "next" in deps:
                        stack.add("Next.js")
                    if "react" in deps:
                        stack.add("React")
                    if "tailwindcss" in deps:
                        stack.add("TailwindCSS")
                    if "express" in deps:
                        stack.add("Express")
                    if "vue" in deps:
                        stack.add("Vue.js")
                except Exception:
                    pass

            # Check requirements.txt
            req_files = list(root_dir.rglob("requirements.txt"))
            for req_path in req_files:
                if "venv" in str(req_path) or ".venv" in str(req_path):
                    continue
                try:
                    text = req_path.read_text(encoding="utf-8", errors="ignore").lower()
                    if "fastapi" in text:
                        stack.add("FastAPI")
                    if "django" in text:
                        stack.add("Django")
                    if "flask" in text:
                        stack.add("Flask")
                    if "torch" in text or "pytorch" in text:
                        stack.add("PyTorch")
                    if "tensorflow" in text:
                        stack.add("TensorFlow")
                    if "google-genai" in text or "google-generativeai" in text:
                        stack.add("Gemini AI")
                except Exception:
                    pass

        if not stack:
            stack.add("Software Repository")

        return sorted(list(stack))

    def register_project(self, session_id: str, summary: Dict[str, Any], source_type: str = "github", url_or_name: str = "") -> Dict[str, Any]:
        with self._get_lock(session_id):
            data = self._load_data(session_id)
            repo_id = summary["repo_id"]
            root_dir = Path(summary["root_path"])

            stack = self.detect_stack(root_dir, summary.get("files", []))
            project_meta = {
                "repo_id": repo_id,
                "name": summary["name"],
                "source_type": source_type,
                "url_or_name": url_or_name or summary["name"],
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "file_count": summary["file_count"],
                "total_lines": summary["total_lines"],
                "stack": stack,
                "tree": summary.get("tree", []),
                "files": summary.get("files", []),
                "root_path": str(root_dir),
            }

            data["projects"][repo_id] = project_meta
            data["active_repo_id"] = repo_id
            self._save_data(session_id, data)

        logger.info(f"Registered project {repo_id} ({summary['name']}) for session '{session_id}' with stack {stack}")
        return project_meta

    def list_projects(self, session_id: str, compact: bool = True) -> List[Dict[str, Any]]:
        data = self._load_data(session_id)
        active_id = data.get("active_repo_id")
        projects = []
        for pid, p in data.get("projects", {}).items():
            p_copy = dict(p)
            p_copy["is_active"] = (pid == active_id)
            if compact:
                p_copy.pop("tree", None)
                p_copy.pop("files", None)
            projects.append(p_copy)
        
        projects.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return projects

    def get_project(self, session_id: str, repo_id: str) -> Optional[Dict[str, Any]]:
        data = self._load_data(session_id)
        return data.get("projects", {}).get(repo_id)

    def set_active_project(self, session_id: str, repo_id: str) -> bool:
        with self._get_lock(session_id):
            data = self._load_data(session_id)
            if repo_id in data.get("projects", {}):
                data["active_repo_id"] = repo_id
                self._save_data(session_id, data)
                return True
            return False

    def get_active_project(self, session_id: str) -> Optional[Dict[str, Any]]:
        data = self._load_data(session_id)
        active_id = data.get("active_repo_id")
        if active_id and active_id in data.get("projects", {}):
            return data["projects"][active_id]
        return None

    def delete_project(self, session_id: str, repo_id: str) -> bool:
        with self._get_lock(session_id):
            data = self._load_data(session_id)
            if repo_id not in data.get("projects", {}):
                logger.warning(f"Attempted to delete non-existent project {repo_id} for session '{session_id}'")
                return False

            # Metadata must never turn a repository ID into an arbitrary path.
            repo_dir = settings.DATA_DIR / "repos" / repo_id if re.fullmatch(r"repo_[a-f0-9]{32}", repo_id) else None
            if repo_dir and repo_dir.exists() and repo_dir.is_dir():
                try:
                    shutil.rmtree(repo_dir)
                    logger.info(f"Deleted repo directory {repo_dir}")
                except Exception as e:
                    logger.error(f"Failed to remove repo dir {repo_dir}: {e}")

            del data["projects"][repo_id]
            if data.get("active_repo_id") == repo_id:
                remaining = list(data["projects"].keys())
                data["active_repo_id"] = remaining[0] if remaining else None
            self._save_data(session_id, data)
        logger.info(f"Deleted project metadata for {repo_id} in session '{session_id}'")
        return True

project_service = ProjectService()
