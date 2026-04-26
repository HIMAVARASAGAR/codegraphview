"""Incremental graph patching logic.

Handles efficient updates when files change — only re-parses affected files
and surgically updates the graph rather than doing a full rebuild.
"""

from __future__ import annotations

import importlib
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Optional

from codegraph.hasher import hash_content
from codegraph.graph.store import GraphStore
from codegraph.graph.exporter import Exporter
from codegraph.parsers.base import Parser

logger = logging.getLogger(__name__)

# ── Parser registry ──────────────────────────────────────────────────

# Lazy registry: maps extensions to (module_path, class_name).
# Parser modules are only imported when a file with that extension is first seen.
_LAZY_PARSER_MAP: dict[str, tuple[str, str]] = {
    ".py": ("codegraph.parsers.python", "PythonParser"),
    ".js": ("codegraph.parsers.javascript", "JavaScriptParser"),
    ".jsx": ("codegraph.parsers.javascript", "JavaScriptParser"),
    ".mjs": ("codegraph.parsers.javascript", "JavaScriptParser"),
    ".cjs": ("codegraph.parsers.javascript", "JavaScriptParser"),
    ".ts": ("codegraph.parsers.typescript", "TypeScriptParser"),
    ".tsx": ("codegraph.parsers.typescript", "TypeScriptParser"),
    ".java": ("codegraph.parsers.java", "JavaParser"),
    ".go": ("codegraph.parsers.go", "GoParser"),
    ".rs": ("codegraph.parsers.rust", "RustParser"),
    ".c": ("codegraph.parsers.c", "CParser"),
    ".h": ("codegraph.parsers.c", "CParser"),
    ".cpp": ("codegraph.parsers.cpp", "CppParser"),
    ".cxx": ("codegraph.parsers.cpp", "CppParser"),
    ".cc": ("codegraph.parsers.cpp", "CppParser"),
    ".hpp": ("codegraph.parsers.cpp", "CppParser"),
    ".hxx": ("codegraph.parsers.cpp", "CppParser"),
    ".hh": ("codegraph.parsers.cpp", "CppParser"),
    ".cs": ("codegraph.parsers.csharp", "CSharpParser"),
    ".rb": ("codegraph.parsers.ruby", "RubyParser"),
    ".php": ("codegraph.parsers.php", "PhpParser"),
    ".swift": ("codegraph.parsers.swift", "SwiftParser"),
    ".kt": ("codegraph.parsers.kotlin", "KotlinParser"),
    ".kts": ("codegraph.parsers.kotlin", "KotlinParser"),
}

# Cache of already-imported parser classes
_PARSER_CLASS_CACHE: dict[str, type[Parser]] = {}
_import_lock = threading.Lock()


def get_parser_for_file(file_path: str) -> Optional[Parser]:
    """Get a parser instance for a file based on its extension.

    Lazily imports the parser module on first use.

    Args:
        file_path: Path to the source file.

    Returns:
        A Parser instance, or None if no parser supports the file type.
    """
    ext = Path(file_path).suffix.lower()
    entry = _LAZY_PARSER_MAP.get(ext)
    if entry is None:
        return None

    mod_path, cls_name = entry

    # Check cache first
    if cls_name in _PARSER_CLASS_CACHE:
        return _PARSER_CLASS_CACHE[cls_name]()

    # Lazy import with lock to prevent concurrent imports of C extensions
    with _import_lock:
        # Double-check after acquiring lock
        if cls_name in _PARSER_CLASS_CACHE:
            return _PARSER_CLASS_CACHE[cls_name]()

        try:
            mod = importlib.import_module(mod_path)
            parser_cls = getattr(mod, cls_name)
            _PARSER_CLASS_CACHE[cls_name] = parser_cls
            return parser_cls()
        except ImportError:
            logger.debug("Parser %s not available (missing tree-sitter grammar)", mod_path)
            return None
        except Exception as e:
            logger.warning("Failed to load parser %s: %s", mod_path, e)
            return None


def has_parser_for_file(file_path: str) -> bool:
    """Check if a parser exists for the given file extension.

    Does NOT import the parser module — only checks the static map.

    Args:
        file_path: Path to the source file.

    Returns:
        True if a parser is registered for this file type.
    """
    ext = Path(file_path).suffix.lower()
    return ext in _LAZY_PARSER_MAP


# ── Patcher ──────────────────────────────────────────────────────────

class Patcher:
    """Incremental graph patcher.

    Processes file change events and updates the graph database.
    Only re-parses files whose content has actually changed.
    """

    def __init__(self, store: GraphStore, repo_root: str | Path,
                 export_path: Optional[str | Path] = None) -> None:
        self.store = store
        self.repo_root = Path(repo_root)
        self.export_path = (Path(export_path) if export_path
                           else self.repo_root / "codegraph" / "graph.json")
        self._exporter = Exporter(store, self.export_path)
        self._lock = threading.Lock()

    def patch_file(self, file_path: str) -> bool:
        """Process a file change. Returns True if graph was updated."""
        path = Path(file_path)
        if not path.exists():
            return False

        try:
            rel_path = str(path.relative_to(self.repo_root))
        except ValueError:
            rel_path = str(path)

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("Cannot read %s: %s", file_path, e)
            return False

        new_hash = hash_content(content)
        stored_hash = self.store.get_file_hash(rel_path)
        if stored_hash == new_hash:
            return False

        parser = get_parser_for_file(rel_path)
        if parser is None:
            return False

        try:
            node_events, edge_events = parser.parse(rel_path, content)
        except Exception as e:
            logger.error("Parse error for %s: %s", rel_path, e)
            return False

        with self._lock:
            self.store.delete_nodes_for_file(rel_path)
            self.store.insert_nodes(node_events, file_hash=new_hash)
            self.store.insert_edges(edge_events)
            self.store.set_file_hash(rel_path, new_hash)

        self._schedule_export()
        logger.info("Patched: %s (%d nodes, %d edges)",
                    rel_path, len(node_events), len(edge_events))
        return True

    def delete_file(self, file_path: str) -> None:
        """Handle a file deletion event."""
        path = Path(file_path)
        try:
            rel_path = str(path.relative_to(self.repo_root))
        except ValueError:
            rel_path = str(path)

        with self._lock:
            self.store.delete_nodes_for_file(rel_path)
            self.store.delete_file_hash(rel_path)

        self._schedule_export()
        logger.info("Deleted from graph: %s", rel_path)

    def _schedule_export(self) -> None:
        """Export graph.json synchronously."""
        try:
            self._exporter.export()
        except Exception as e:
            logger.error("Export failed: %s", e)


# ── Worker pool ──────────────────────────────────────────────────────

class PatchWorkerPool:
    """Thread pool for processing file change events from a queue."""

    def __init__(self, patcher: Patcher, num_workers: int = 4) -> None:
        workers = int(os.environ.get("CODEGRAPH_WORKERS", num_workers))
        self.patcher = patcher
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()
        for i in range(workers):
            t = threading.Thread(target=self._loop, name=f"cg-worker-{i}", daemon=True)
            self._workers.append(t)

    def start(self) -> None:
        """Start all worker threads."""
        for t in self._workers:
            t.start()

    def stop(self) -> None:
        """Signal workers to stop and wait."""
        self._stop.set()
        for _ in self._workers:
            self.queue.put(("STOP", ""))
        for t in self._workers:
            t.join(timeout=5.0)

    def submit(self, event_type: str, file_path: str) -> None:
        """Submit a file change event."""
        self.queue.put((event_type, file_path))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                etype, fpath = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if etype == "STOP":
                break
            try:
                if etype == "deleted":
                    self.patcher.delete_file(fpath)
                else:
                    self.patcher.patch_file(fpath)
            except Exception as e:
                logger.error("Worker error %s (%s): %s", fpath, etype, e)
            finally:
                self.queue.task_done()
