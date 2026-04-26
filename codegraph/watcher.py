"""File system watcher for incremental graph updates.

Uses watchdog to monitor the repository for file changes and feeds events
into the patcher's worker pool. Implements 50ms debouncing per file.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from codegraph.graph.store import GraphStore
from codegraph.graph.patcher import Patcher, PatchWorkerPool, has_parser_for_file

logger = logging.getLogger(__name__)

# Debounce interval in seconds
_DEBOUNCE_INTERVAL = 0.05  # 50ms


def _load_ignore_patterns(repo_root: Path) -> list[str]:
    """Load ignore patterns from .codegraphignore."""
    ignore_file = repo_root / ".codegraphignore"
    if not ignore_file.exists():
        return []
    patterns = []
    for line in ignore_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _should_ignore(file_path: str, patterns: list[str]) -> bool:
    """Check if a file should be ignored based on patterns."""
    import fnmatch
    for pattern in patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
        if fnmatch.fnmatch(file_path, f"*/{pattern}"):
            return True
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            parts = Path(file_path).parts
            if dir_name in parts:
                return True
    return False


class _CodeGraphHandler(FileSystemEventHandler):
    """Watchdog event handler that debounces and forwards events to the patcher."""

    def __init__(self, pool: PatchWorkerPool, repo_root: Path, patterns: list[str]) -> None:
        super().__init__()
        self.pool = pool
        self.repo_root = repo_root
        self.patterns = patterns
        self._last_event: dict[str, float] = {}
        self._lock = threading.Lock()

    def _should_process(self, path: str) -> bool:
        """Check debounce and ignore rules."""
        # Ignore directories and non-parseable files
        if Path(path).is_dir():
            return False

        try:
            rel = str(Path(path).relative_to(self.repo_root))
        except ValueError:
            return False

        if _should_ignore(rel, self.patterns):
            return False

        if not has_parser_for_file(rel):
            return False

        # Debounce
        now = time.time()
        with self._lock:
            last = self._last_event.get(path, 0)
            if now - last < _DEBOUNCE_INTERVAL:
                return False
            self._last_event[path] = now

        return True

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification events."""
        if not event.is_directory and self._should_process(event.src_path):
            self.pool.submit("modified", event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation events."""
        if not event.is_directory and self._should_process(event.src_path):
            self.pool.submit("created", event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file deletion events."""
        if not event.is_directory:
            try:
                rel = str(Path(event.src_path).relative_to(self.repo_root))
            except ValueError:
                return
            if not _should_ignore(rel, self.patterns):
                self.pool.submit("deleted", event.src_path)


def start_watcher(store: GraphStore, repo_root: Path) -> None:
    """Start watching a repository for file changes.

    Blocks until interrupted (e.g. KeyboardInterrupt).

    Args:
        store: The GraphStore to update.
        repo_root: Root directory to watch.
    """
    patcher = Patcher(store, repo_root)
    pool = PatchWorkerPool(patcher)
    pool.start()

    patterns = _load_ignore_patterns(repo_root)
    handler = _CodeGraphHandler(pool, repo_root, patterns)

    observer = Observer()
    observer.schedule(handler, str(repo_root), recursive=True)
    observer.start()

    logger.info("Watching %s for changes...", repo_root)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        pool.stop()
        logger.info("Watcher stopped.")
