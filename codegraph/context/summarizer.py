"""Lazy AI summary generation (Phase 2 placeholder).

This module will eventually generate AI summaries for nodes on demand.
For now, it returns None — ai_summary is left null as specified.
"""

from __future__ import annotations

from typing import Optional


class Summarizer:
    """Placeholder for AI-powered code summarization.

    Phase 2 will implement actual LLM-based summarization.
    For now all methods return None.
    """

    def summarize_node(self, node_id: str, code: str) -> Optional[str]:
        """Generate an AI summary for a code node.

        Args:
            node_id: The node to summarize.
            code: The source code of the node.

        Returns:
            None (Phase 2 will return actual summaries).
        """
        return None

    def summarize_file(self, file_path: str, content: str) -> Optional[str]:
        """Generate an AI summary for a file.

        Args:
            file_path: Path to the file.
            content: Full file content.

        Returns:
            None (Phase 2 will return actual summaries).
        """
        return None
