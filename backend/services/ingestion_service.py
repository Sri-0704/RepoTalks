import ast
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from backend.config import settings

logger = logging.getLogger(__name__)

# Supported text extensions to analyze
TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md",
    ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".sql", ".sh", ".yaml", ".yml", ".env.example"
}

IGNORE_DIRS = {
    "node_modules", "venv", ".venv", ".git", "__pycache__", "dist", "build", ".next", ".idea", ".vscode"
}

GITHUB_REPOSITORY_URL = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class IngestionError(ValueError):
    """Raised when an import is malformed or exceeds the configured limits."""


class UploadTooLargeError(IngestionError):
    """Raised when an uploaded archive exceeds an enforced size limit."""


def empty_symbol_dict() -> Dict[str, Any]:
    """Return a fresh canonical empty symbol structure."""
    return {
        "functions": [],
        "classes": [],
        "imports": [],
    }


class IndexingPolicy:
    """Evaluates whether a repository file should be parsed into semantic RAG chunks.

    Files designated as non-indexable (such as lockfiles or minified bundles)
    remain preserved in repository metadata and the file explorer tree with:
        indexed: False
        index_skip_reason: "generated_dependency_file" | "generated_minified_asset"
    """

    IGNORED_LOCKFILE_NAMES = {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "poetry.lock",
        "pipfile.lock",
        "uv.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
    }

    IGNORED_GENERATED_SUFFIXES = (
        ".min.js",
        ".min.css",
        ".map",
        ".bundle.js",
        ".bundle.css",
    )

    @classmethod
    def evaluate_file(cls, filename_or_path: str) -> Tuple[bool, Optional[str]]:
        """Evaluate a filename or path (case-insensitive) for semantic chunking eligibility."""
        base_name = Path(filename_or_path).name.lower()
        if base_name in cls.IGNORED_LOCKFILE_NAMES:
            return False, "generated_dependency_file"
        for suffix in cls.IGNORED_GENERATED_SUFFIXES:
            if base_name.endswith(suffix):
                return False, "generated_minified_asset"
        return True, None


class IngestionService:
    def process_github_url(self, repo_url: str, repo_id: Optional[str] = None) -> Dict[str, Any]:
        cleaned_url = repo_url.strip()
        match = GITHUB_REPOSITORY_URL.fullmatch(cleaned_url)
        if not match:
            raise IngestionError("Use a public HTTPS GitHub repository URL such as https://github.com/owner/repository.")
        repo_name = match.group("repo")
        repo_id = repo_id or self.new_repo_id()

        dest_dir = self._create_staging_directory(repo_id)

        git_env = os.environ.copy()
        git_env["GIT_TERMINAL_PROMPT"] = "0"
        git_env["GIT_ASKPASS"] = "echo"
        git_env["GCM_INTERACTIVE"] = "never"

        logger.info(f"Cloning GitHub repository {cleaned_url} to {dest_dir}")
        try:
            res = subprocess.run(
                ["git", "clone", "--depth=1", "--", cleaned_url, str(dest_dir)],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                env=git_env,
                timeout=settings.GITHUB_CLONE_TIMEOUT_SECONDS,
            )
            if res.returncode != 0:
                err_msg = res.stderr.strip() if res.stderr else "Unknown git clone error."
                if any(x in err_msg for x in ("could not read Username", "terminal prompts disabled", "Authentication failed", "Repository not found")):
                    raise IngestionError(
                        "Repository not found or requires authentication. Please ensure the repository is public and the URL is correct."
                    )
                raise IngestionError(f"Git clone failed: {err_msg}")
        except subprocess.TimeoutExpired as exc:
            self.discard_staged_repository(dest_dir)
            raise IngestionError(
                f"Repository clone timed out after {settings.GITHUB_CLONE_TIMEOUT_SECONDS} seconds."
            ) from exc
        except IngestionError:
            self.discard_staged_repository(dest_dir)
            raise
        except Exception as exc:
            self.discard_staged_repository(dest_dir)
            raise IngestionError(f"Failed to clone repository: {exc}") from exc

        # Remove .git metadata directory: saves disk and prevents Windows packfile lock errors
        git_dir = dest_dir / ".git"
        if git_dir.exists():
            self._rmtree_force(git_dir)

        summary = self.analyze_repository(dest_dir, repo_id, name=repo_name)
        summary["_storage_dir"] = str(dest_dir)
        return summary

    def process_zip_upload(self, zip_path: Path, filename: str, repo_id: Optional[str] = None) -> Dict[str, Any]:
        safe_filename = Path(filename or "upload.zip").name
        if Path(safe_filename).suffix.lower() != ".zip":
            raise IngestionError("Only .zip archives are supported.")
        repo_name = Path(safe_filename).stem or "uploaded-repository"
        repo_id = repo_id or self.new_repo_id()
        dest_dir = self._create_staging_directory(repo_id)

        try:
            self._extract_zip_safely(zip_path, dest_dir)
        except Exception:
            self.discard_staged_repository(dest_dir)
            raise

        # Check if single top-level directory extracted
        extracted_items = [d for d in dest_dir.iterdir() if d.is_dir()]
        if len(extracted_items) == 1 and len(list(dest_dir.glob("*"))) == 1:
            target_dir = extracted_items[0]
        else:
            target_dir = dest_dir

        summary = self.analyze_repository(target_dir, repo_id, name=repo_name)
        summary["_storage_dir"] = str(dest_dir)
        return summary

    def new_repo_id(self) -> str:
        return f"repo_{uuid.uuid4().hex}"

    def _create_staging_directory(self, repo_id: str) -> Path:
        if not re.fullmatch(r"repo_[a-f0-9]{32}", repo_id):
            raise IngestionError("Invalid repository identifier.")
        dest_dir = (settings.DATA_DIR / "staging" / repo_id).resolve()
        if dest_dir.exists():
            raise IngestionError("Repository import is already in progress.")
        dest_dir.mkdir(parents=True, exist_ok=False)
        return dest_dir

    def promote_staged_repository(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        repo_id = summary["repo_id"]
        storage_dir = Path(summary["_storage_dir"]).resolve()
        repository_root = Path(summary["root_path"]).resolve()
        try:
            root_relative_path = repository_root.relative_to(storage_dir)
        except ValueError as exc:
            raise IngestionError("Repository import escaped its staging directory.") from exc
        final_dir = (settings.DATA_DIR / "repos" / repo_id).resolve()
        if final_dir.exists():
            raise IngestionError("A repository with this identifier already exists.")
        try:
            storage_dir.replace(final_dir)
        except OSError:
            shutil.move(str(storage_dir), str(final_dir))
        summary.pop("_storage_dir", None)
        summary["root_path"] = str((final_dir / root_relative_path).resolve())
        return summary

    def discard_staged_repository(self, storage_dir: Path) -> None:
        staging_root = (settings.DATA_DIR / "staging").resolve()
        try:
            storage_dir.resolve().relative_to(staging_root)
        except ValueError:
            logger.error("Refused to delete a path outside the staging directory: %s", storage_dir)
            return
        if storage_dir.exists():
            self._rmtree_force(storage_dir)

    def discard_promoted_repository(self, repo_id: str) -> None:
        """Remove only a generated repository directory after a failed import transaction."""
        if not re.fullmatch(r"repo_[a-f0-9]{32}", repo_id):
            logger.error("Refused to delete a repository with an invalid identifier: %s", repo_id)
            return
        repos_root = (settings.DATA_DIR / "repos").resolve()
        repo_dir = (repos_root / repo_id).resolve()
        try:
            repo_dir.relative_to(repos_root)
        except ValueError:
            logger.error("Refused to delete a path outside the repositories directory: %s", repo_dir)
            return
        if repo_dir.exists():
            self._rmtree_force(repo_dir)

    @staticmethod
    def _rmtree_force(target_dir: Path) -> None:
        """Recursively remove a directory tree, clearing Windows read-only flags on git packfiles."""
        import stat

        def _on_error(func, path, _):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        try:
            shutil.rmtree(target_dir, onexc=lambda func, p, exc: _on_error(func, p, exc))
        except TypeError:
            shutil.rmtree(target_dir, onerror=_on_error)
        except Exception as exc:
            logger.warning("Could not completely remove %s: %s", target_dir, exc)

    def _extract_zip_safely(self, zip_path: Path, destination: Path) -> None:
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                members = archive.infolist()
                if len(members) > settings.MAX_ARCHIVE_FILES:
                    raise UploadTooLargeError("Archive contains too many files.")
                total_size = sum(member.file_size for member in members)
                if total_size > settings.MAX_EXTRACTED_SIZE_MB * 1024 * 1024:
                    raise UploadTooLargeError("Archive expands beyond the allowed size.")

                destination_root = destination.resolve()
                for member in members:
                    member_name = member.filename.replace("\\", "/")
                    member_path = PurePosixPath(member_name)
                    is_symlink = (member.external_attr >> 16) & 0o170000 == 0o120000
                    if member_path.is_absolute() or ".." in member_path.parts or is_symlink:
                        raise IngestionError("Archive contains an unsafe path.")
                    if member.is_dir():
                        continue
                    if member.file_size > settings.MAX_SOURCE_FILE_SIZE_MB * 1024 * 1024:
                        raise UploadTooLargeError("Archive contains a file that is too large.")
                    target = (destination / Path(*member_path.parts)).resolve()
                    try:
                        target.relative_to(destination_root)
                    except ValueError as exc:
                        raise IngestionError("Archive contains an unsafe path.") from exc
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, "r") as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        except zipfile.BadZipFile as exc:
            raise IngestionError("The upload is not a valid ZIP archive.") from exc

    def analyze_repository(self, root_dir: Path, repo_id: str, name: str) -> Dict[str, Any]:
        files_list = []
        chunks = []
        modules = []
        total_lines = 0
        skipped_files_count = 0
        skipped_chars_count = 0

        root_dir = root_dir.resolve()
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # In-place directory pruning: NEVER descend into ignored directories
            dirnames[:] = [
                d for d in dirnames
                if d not in IGNORE_DIRS and not (d.startswith(".") and d not in {".github"})
            ]

            for filename in filenames:
                path = Path(dirpath) / filename
                if path.is_symlink():
                    continue
                try:
                    path.resolve().relative_to(root_dir)
                except ValueError:
                    continue
                rel_path = path.relative_to(root_dir).as_posix()

                ext = ".env.example" if filename == ".env.example" else path.suffix.lower()
                if ext not in TEXT_EXTENSIONS:
                    continue

                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                lines = content.splitlines()
                line_count = len(lines)
                total_lines += line_count

                # Evaluate indexing policy for semantic RAG eligibility
                is_indexable, skip_reason = IndexingPolicy.evaluate_file(filename)

                # AST or regex parsing for symbol extraction
                symbols = self.extract_symbols(content, rel_path, ext) if is_indexable else empty_symbol_dict()
                files_list.append({
                    "path": rel_path,
                    "size_bytes": path.stat().st_size,
                    "line_count": line_count,
                    "extension": ext,
                    "symbols": symbols,
                    "indexed": is_indexable,
                    "index_skip_reason": skip_reason,
                })

                if is_indexable:
                    # Chunk file content for RAG indexing
                    file_chunks = self.chunk_file_content(rel_path, content, symbols)
                    chunks.extend(file_chunks)
                else:
                    skipped_files_count += 1
                    skipped_chars_count += len(content)

        # Build directory tree hierarchy (all safe repository files remain visible)
        tree = self.build_file_tree([f["path"] for f in files_list])

        summary = {
            "repo_id": repo_id,
            "name": name,
            "root_path": str(root_dir),
            "file_count": len(files_list),
            "indexed_file_count": len(files_list) - skipped_files_count,
            "total_lines": total_lines,
            "files": files_list,
            "tree": tree,
            "chunks": chunks,
            "skipped_indexing_stats": {
                "file_count": skipped_files_count,
                "char_count": skipped_chars_count,
            },
        }
        logger.info(
            "Repository %s analyzed: %d files (%d indexed, %d non-indexed generated/lockfiles), %d chunks (~%d chars omitted from vector index)",
            repo_id,
            len(files_list),
            len(files_list) - skipped_files_count,
            skipped_files_count,
            len(chunks),
            skipped_chars_count,
        )

        # Cache analyzed metadata
        meta_file = root_dir / ".repotalks_meta.json"
        try:
            meta_file.write_text(json.dumps({
                "repo_id": repo_id,
                "name": name,
                "file_count": len(files_list),
                "total_lines": total_lines
            }, indent=2))
        except Exception:
            pass

        return summary

    def extract_symbols(self, content: str, file_path: str, ext: str) -> Dict[str, Any]:
        functions = []
        classes = []
        imports = []

        if ext == ".py":
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions.append({
                            "name": node.name,
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "docstring": ast.get_docstring(node) or ""
                        })
                    elif isinstance(node, ast.ClassDef):
                        classes.append({
                            "name": node.name,
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "docstring": ast.get_docstring(node) or ""
                        })
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)
            except Exception:
                pass
        elif ext in {".js", ".jsx", ".ts", ".tsx"}:
            # Regex parser for JS/TS functions & classes & imports
            fn_matches = re.finditer(r'(?:function\s+([a-zA-Z0-9_$]+)|(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)', content)
            for m in fn_matches:
                fn_name = m.group(1) or m.group(2)
                if fn_name:
                    start_l = self._line_for_match(content, m)
                    functions.append({"name": fn_name, "line": start_l, "end_line": start_l})
            
            cls_matches = re.finditer(r'class\s+([a-zA-Z0-9_$]+)', content)
            for c in cls_matches:
                start_l = self._line_for_match(content, c)
                classes.append({"name": c.group(1), "line": start_l, "end_line": start_l})

            imp_matches = re.findall(r'import\s+.*?from\s+[\'"](.*?)[\'"]', content)
            imports.extend(imp_matches)
        elif ext == ".java":
            for match in re.finditer(r"\bclass\s+([A-Za-z_$][\w$]*)", content):
                start_l = self._line_for_match(content, match)
                classes.append({"name": match.group(1), "line": start_l, "end_line": start_l})
            for match in re.finditer(r"(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+([A-Za-z_$][\w$]*)\s*\(", content):
                start_l = self._line_for_match(content, match)
                functions.append({"name": match.group(1), "line": start_l, "end_line": start_l})
            imports.extend(re.findall(r"\bimport\s+([\w.]+)", content))

        return {
            "functions": functions,
            "classes": classes,
            "imports": list(set(imports))
        }

    def chunk_file_content(
        self,
        file_path: str,
        content: str,
        symbols: Dict[str, Any],
        max_chunk_lines: int = 50,
        max_chunk_bytes: int = 2500,
        overlap_lines: int = 5,
    ) -> List[Dict[str, Any]]:
        lines = content.splitlines()
        total_lines = len(lines)
        if not lines or total_lines == 0:
            return []

        # Gather symbol spans that have known end lines (e.g. Python AST)
        # Class spans and function spans
        spans: List[Tuple[int, int, str, str]] = []
        for fn in symbols.get("functions", []):
            start = fn["line"]
            end = fn.get("end_line", start)
            if 1 <= start <= total_lines and end >= start:
                spans.append((start, min(end, total_lines), "function", fn["name"]))
        for cl in symbols.get("classes", []):
            start = cl["line"]
            end = cl.get("end_line", start)
            if 1 <= start <= total_lines and end >= start:
                spans.append((start, min(end, total_lines), "class", cl["name"]))

        # Sort spans by start line
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))

        # Determine line ranges for chunks
        ranges: List[Tuple[int, int]] = []

        if spans and any(s[1] > s[0] for s in spans):
            # Symbol-boundary chunking
            curr_line = 1
            for start, end, stype, sname in spans:
                if start < curr_line:
                    continue  # nested inside an already handled span

                # Gap before this symbol (e.g. module header, imports)
                if start > curr_line:
                    gap_start = curr_line
                    gap_end = start - 1
                    for g_idx in range(gap_start, gap_end + 1, max_chunk_lines):
                        g_chunk_end = min(g_idx + max_chunk_lines - 1, gap_end)
                        ranges.append((g_idx, g_chunk_end))

                # Now chunk the symbol itself
                span_len = end - start + 1
                if span_len <= max_chunk_lines:
                    ranges.append((start, end))
                else:
                    # Large symbol: sub-chunk with overlap
                    step = max(max_chunk_lines - overlap_lines, 1)
                    for s_idx in range(start, end + 1, step):
                        s_chunk_end = min(s_idx + max_chunk_lines - 1, end)
                        ranges.append((s_idx, s_chunk_end))
                        if s_chunk_end >= end:
                            break

                curr_line = end + 1

            # Trailing gap after last symbol
            if curr_line <= total_lines:
                for t_idx in range(curr_line, total_lines + 1, max_chunk_lines):
                    t_chunk_end = min(t_idx + max_chunk_lines - 1, total_lines)
                    ranges.append((t_idx, t_chunk_end))
        else:
            # Fallback: logical block / paragraph boundary chunking
            curr_start = 1
            idx = 0
            while idx < total_lines:
                chunk_start = curr_start
                chunk_end = min(chunk_start + max_chunk_lines - 1, total_lines)

                # Look for a natural break (empty line) near the end if not at EOF
                if chunk_end < total_lines:
                    # Search backwards up to 10 lines for an empty line
                    search_from = chunk_end
                    search_to = max(chunk_start + 10, chunk_end - 10)
                    for candidate in range(search_from, search_to, -1):
                        if candidate - 1 < len(lines) and not lines[candidate - 1].strip():
                            chunk_end = candidate
                            break

                ranges.append((chunk_start, chunk_end))
                curr_start = chunk_end + 1
                idx = chunk_end

        # Build chunk dictionaries
        chunks = []
        for chunk_idx, (start_line, end_line) in enumerate(ranges):
            chunk_slice = lines[start_line - 1 : end_line]
            chunk_text = "\n".join(chunk_slice)

            # Check byte ceiling: truncate lines if exceeding max_chunk_bytes
            encoded = chunk_text.encode("utf-8")
            if len(encoded) > max_chunk_bytes:
                # Keep as many lines as fit within max_chunk_bytes
                accum = 0
                trimmed_slice = []
                for l in chunk_slice:
                    l_bytes = len(l.encode("utf-8")) + 1
                    if accum + l_bytes > max_chunk_bytes and trimmed_slice:
                        break
                    trimmed_slice.append(l)
                    accum += l_bytes
                chunk_slice = trimmed_slice
                end_line = start_line + len(chunk_slice) - 1
                chunk_text = "\n".join(chunk_slice)

            content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:16]

            # Filter functions and classes relevant to this chunk's line range
            chunk_fns = [
                f["name"] for f in symbols.get("functions", [])
                if not (f.get("end_line", f["line"]) < start_line or f["line"] > end_line)
            ]
            chunk_cls = [
                c["name"] for c in symbols.get("classes", [])
                if not (c.get("end_line", c["line"]) < start_line or c["line"] > end_line)
            ]

            chunk_id = f"{file_path}_chunk_{chunk_idx}"
            chunks.append({
                "chunk_id": chunk_id,
                "chunk_order": chunk_idx,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "chunk_text": f"File: {file_path} (Lines {start_line}-{end_line})\n\n{chunk_text}",
                "content_hash": content_hash,
                "metadata": {
                    "file_path": file_path,
                    "functions": chunk_fns,
                    "classes": chunk_cls,
                    "content_hash": content_hash,
                    "start_line": start_line,
                    "end_line": end_line,
                    "line_count": len(chunk_slice),
                }
            })

        return chunks

    def build_file_tree(self, paths: List[str]) -> List[Dict[str, Any]]:
        tree_dict = {}
        for path in paths:
            parts = PurePosixPath(path.replace("\\", "/")).parts
            curr = tree_dict
            for i, part in enumerate(parts):
                if part not in curr:
                    curr[part] = {"_path": "/".join(parts[:i+1]), "_is_file": (i == len(parts) - 1)}
                curr = curr[part]

        def format_tree(node_dict: dict) -> list:
            result = []
            child_items = [
                (k, v) for k, v in node_dict.items() 
                if k not in {"_path", "_is_file"} and isinstance(v, dict)
            ]
            sorted_items = sorted(child_items, key=lambda item: (item[1].get("_is_file", False), item[0]))
            
            for name, val in sorted_items:
                is_file = val.get("_is_file", False)
                children = format_tree(val) if not is_file else None
                result.append({
                    "name": name,
                    "path": val.get("_path", name),
                    "is_file": is_file,
                    "children": children
                })
            return result

        return format_tree(tree_dict)

    @staticmethod
    def _line_for_match(content: str, match: re.Match) -> int:
        return content[:match.start()].count("\n") + 1

ingestion_service = IngestionService()
