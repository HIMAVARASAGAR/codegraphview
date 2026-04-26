"""Intermediate Representation (IR) dataclasses and abstract Parser base class.

Every language parser emits NodeEvent and EdgeEvent instances — nothing else.
All downstream code (graph storage, querying, export) is language-agnostic.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Optional

# ── Type aliases ──────────────────────────────────────────────────────────────

NodeKind = Literal["FILE", "MODULE", "CLASS", "FUNCTION", "VARIABLE", "IMPORT"]
EdgeKind = Literal["DEFINES", "CALLS", "IMPORTS", "INHERITS", "INSTANTIATES", "READS", "WRITES"]


# ── IR dataclasses ────────────────────────────────────────────────────────────

@dataclass
class NodeEvent:
    """A discovered code entity (file, class, function, variable, import)."""

    kind: NodeKind
    name: str
    file_path: str
    line_start: int
    line_end: int
    signature: Optional[str] = None
    parent_id: Optional[str] = None  # e.g. class this function belongs to
    language: str = ""
    is_async: bool = False
    is_exported: bool = False
    decorators: list[str] = field(default_factory=list)


@dataclass
class EdgeEvent:
    """A relationship between two nodes (calls, imports, inherits, etc.)."""

    kind: EdgeKind
    from_id: str  # sha256(file_path + "::" + name + "::" + kind)
    to_id: str
    line: int
    confidence: float = 1.0  # 1.0 = certain, 0.7 = inferred/dynamic


# ── Node ID generation ────────────────────────────────────────────────────────

def make_node_id(file_path: str, name: str, kind: str) -> str:
    """Generate a deterministic 16-char hex ID for a node.

    Args:
        file_path: Path to the source file.
        name: Name of the code entity.
        kind: NodeKind value (FILE, FUNCTION, CLASS, etc.).

    Returns:
        A 16-character hex string derived from SHA-256.
    """
    raw = f"{file_path}::{name}::{kind}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Abstract parser ──────────────────────────────────────────────────────────

class Parser(ABC):
    """Abstract base class for language parsers.

    Each language implements this interface. The parser receives raw source code
    and emits a list of NodeEvents and EdgeEvents that describe the code structure.
    """

    language: str = ""
    extensions: list[str] = []

    @abstractmethod
    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse source code and emit IR events.

        Args:
            file_path: Path to the source file being parsed.
            content: Raw source code content.

        Returns:
            A tuple of (node_events, edge_events).
        """
        ...
