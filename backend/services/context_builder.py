"""Common Context Builder for RepoTalk.

Standardizes context formatting, citation extraction, and token budget
accounting across all services (Chat, Architecture, Audience, Tracer, Viva).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Approximate 4 characters per token for source code / English text
CHARS_PER_TOKEN = 4


@dataclass
class ContextResult:
    context_text: str
    citations: List[Dict[str, Any]] = field(default_factory=list)
    total_chunks: int = 0
    estimated_tokens: int = 0
    files_included: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return self.total_chunks == 0


class ContextBuilder:
    """Unified context formatter and token budget accountant."""

    def build_context(
        self,
        chunks: List[Dict[str, Any]],
        max_tokens: int = 6000,
        max_chars: Optional[int] = None,
        include_scores: bool = False,
    ) -> ContextResult:
        """Build a formatted context block respecting a strict token/char budget.

        Parameters
        ----------
        chunks : List[Dict[str, Any]]
            Search result chunks (from vector_store.search / search_hybrid / search_semantic).
        max_tokens : int
            Maximum token budget for context (default 6000 tokens ≈ 24000 characters).
        max_chars : Optional[int]
            Explicit character limit override.
        include_scores : bool
            Whether to include relevance score in chunk headers.
        """
        char_budget = max_chars if max_chars is not None else (max_tokens * CHARS_PER_TOKEN)

        if not chunks:
            return ContextResult(context_text="No relevant codebase snippets found.", total_chunks=0, estimated_tokens=0)

        # Deduplicate while preserving rank order
        seen_keys = set()
        unique_chunks: List[Dict[str, Any]] = []
        for c in chunks:
            key = (c.get("file_path", ""), c.get("chunk_id", ""))
            if key not in seen_keys:
                seen_keys.add(key)
                unique_chunks.append(c)

        accumulated_parts: List[str] = []
        citations: List[Dict[str, Any]] = []
        files_included: List[str] = []
        used_chars = 0

        for chunk in unique_chunks:
            file_path = chunk.get("file_path", "unknown")
            chunk_id = chunk.get("chunk_id", "")
            raw_text = chunk.get("chunk_text", "").strip()
            similarity = chunk.get("similarity", 0.0)
            meta = chunk.get("metadata", {})

            # Extract line range if available
            start_line = meta.get("start_line") or chunk.get("start_line")
            end_line = meta.get("end_line") or chunk.get("end_line")
            line_str = f" (Lines {start_line}-{end_line})" if start_line and end_line else ""

            # Score annotation
            score_str = f" [Relevance: {similarity:.2f}]" if include_scores and similarity > 0 else ""

            block = (
                f'<file_snippet path="{file_path}"{line_str}{score_str} untrusted="true">\n'
                f"{raw_text}\n"
                f"</file_snippet>\n"
            )

            block_len = len(block)
            if used_chars + block_len > char_budget and accumulated_parts:
                # If adding this block exceeds budget and we already have at least one block, stop
                logger.debug("Context budget reached: %d/%d chars used", used_chars, char_budget)
                break

            accumulated_parts.append(block)
            used_chars += block_len

            citations.append({
                "file_path": file_path,
                "chunk_id": chunk_id,
                "start_line": start_line,
                "end_line": end_line,
                "similarity": round(float(similarity), 3),
                "is_exact": chunk.get("exact_match", False),
                "is_keyword": chunk.get("keyword_match", False),
            })

            if file_path not in files_included:
                files_included.append(file_path)

        snippets_body = "\n".join(accumulated_parts)
        context_text = f'<codebase_context untrusted="true">\n{snippets_body}\n</codebase_context>'
        estimated_tokens = len(context_text) // CHARS_PER_TOKEN

        return ContextResult(
            context_text=context_text,
            citations=citations,
            total_chunks=len(accumulated_parts),
            estimated_tokens=estimated_tokens,
            files_included=files_included,
        )


context_builder = ContextBuilder()
