"""Plugin base class for CodeGraph extensions."""
from __future__ import annotations
from abc import ABC, abstractmethod


class Plugin(ABC):
    """Abstract base class for CodeGraph plugins.

    Plugins can hook into graph events (scan, patch, export)
    and add custom analysis or transformation logic.
    """

    name: str = ""
    version: str = "0.1.0"

    @abstractmethod
    def on_scan_complete(self, stats: dict) -> None:
        """Called after a full scan completes.

        Args:
            stats: Graph statistics dict.
        """
        ...

    @abstractmethod
    def on_file_patched(self, file_path: str, node_count: int, edge_count: int) -> None:
        """Called after a file is patched.

        Args:
            file_path: Path to the patched file.
            node_count: Number of nodes emitted.
            edge_count: Number of edges emitted.
        """
        ...
