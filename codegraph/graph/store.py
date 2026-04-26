"""SQLite-backed graph storage.

Manages the persistent code knowledge graph in a local SQLite database.
All schema creation, node/edge CRUD, and file hash tracking live here.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from codegraph.parsers.base import NodeEvent, EdgeEvent, make_node_id

# ── Schema SQL ────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    line_start  INTEGER,
    line_end    INTEGER,
    signature   TEXT,
    language    TEXT,
    file_hash   TEXT,
    ai_summary  TEXT,
    complexity  INTEGER,
    is_async    INTEGER DEFAULT 0,
    is_exported INTEGER DEFAULT 0,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    from_id     TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    line        INTEGER,
    confidence  REAL DEFAULT 1.0,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS file_hashes (
    file_path   TEXT PRIMARY KEY,
    hash        TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_from     ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to       ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_nodes_file     ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_name     ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind     ON nodes(kind);
"""


class GraphStore:
    """SQLite-backed storage for the code knowledge graph.

    Handles schema initialization, node/edge persistence, file hash tracking,
    and basic read operations. Thread-safe via SQLite's WAL mode.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the graph store.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    # ── Connection management ─────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open a connection and ensure schema exists.

        Returns:
            The active SQLite connection.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get the active connection, opening one if necessary."""
        return self.connect()

    # ── Node operations ───────────────────────────────────────────────────

    def insert_node(self, event: NodeEvent, file_hash: str = "") -> str:
        """Insert a node into the graph.

        Args:
            event: The NodeEvent describing the code entity.
            file_hash: SHA-256 hash of the source file.

        Returns:
            The generated node ID.
        """
        node_id = make_node_id(event.file_path, event.name, event.kind)
        now = int(time.time())
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO nodes
                   (id, kind, name, file_path, line_start, line_end, signature,
                    language, file_hash, ai_summary, complexity, is_async,
                    is_exported, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node_id,
                    event.kind,
                    event.name,
                    event.file_path,
                    event.line_start,
                    event.line_end,
                    event.signature,
                    event.language,
                    file_hash,
                    None,  # ai_summary — Phase 2
                    None,  # complexity — computed later
                    int(event.is_async),
                    int(event.is_exported),
                    now,
                ),
            )
        return node_id

    def insert_nodes(self, events: list[NodeEvent], file_hash: str = "") -> list[str]:
        """Insert multiple nodes in a single transaction.

        Args:
            events: List of NodeEvents to insert.
            file_hash: SHA-256 hash of the source file.

        Returns:
            List of generated node IDs.
        """
        ids = []
        now = int(time.time())
        with self._lock:
            for event in events:
                node_id = make_node_id(event.file_path, event.name, event.kind)
                self.conn.execute(
                    """INSERT OR REPLACE INTO nodes
                       (id, kind, name, file_path, line_start, line_end, signature,
                        language, file_hash, ai_summary, complexity, is_async,
                        is_exported, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        node_id,
                        event.kind,
                        event.name,
                        event.file_path,
                        event.line_start,
                        event.line_end,
                        event.signature,
                        event.language,
                        file_hash,
                        None,
                        None,
                        int(event.is_async),
                        int(event.is_exported),
                        now,
                    ),
                )
                ids.append(node_id)
            self.conn.commit()
        return ids

    # ── Edge operations ───────────────────────────────────────────────────

    def insert_edge(self, event: EdgeEvent) -> str:
        """Insert an edge into the graph.

        Skips edges where to_id does not reference an existing node (unresolved refs).

        Args:
            event: The EdgeEvent describing the relationship.

        Returns:
            The generated edge ID, or empty string if skipped.
        """
        with self._lock:
            # Check if to_id exists in nodes
            row = self.conn.execute("SELECT 1 FROM nodes WHERE id = ?", (event.to_id,)).fetchone()
            if row is None:
                return ""

            edge_id = make_node_id(event.from_id, event.to_id, event.kind)
            now = int(time.time())
            self.conn.execute(
                """INSERT OR REPLACE INTO edges
                   (id, kind, from_id, to_id, line, confidence, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (edge_id, event.kind, event.from_id, event.to_id, event.line, event.confidence, now),
            )
        return edge_id

    def insert_edges(self, events: list[EdgeEvent]) -> list[str]:
        """Insert multiple edges, skipping unresolved references.

        Args:
            events: List of EdgeEvents to insert.

        Returns:
            List of inserted edge IDs (empty strings for skipped edges).
        """
        with self._lock:
            # Pre-fetch all node IDs for fast lookup
            existing = {
                row[0]
                for row in self.conn.execute("SELECT id FROM nodes").fetchall()
            }
            ids = []
            now = int(time.time())
            for event in events:
                if event.to_id not in existing:
                    ids.append("")
                    continue
                edge_id = make_node_id(event.from_id, event.to_id, event.kind)
                self.conn.execute(
                    """INSERT OR REPLACE INTO edges
                       (id, kind, from_id, to_id, line, confidence, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (edge_id, event.kind, event.from_id, event.to_id, event.line, event.confidence, now),
                )
                ids.append(edge_id)
            self.conn.commit()
        return ids

    # ── File hash operations ──────────────────────────────────────────────

    def get_file_hash(self, file_path: str) -> Optional[str]:
        """Get the stored hash for a file.

        Args:
            file_path: Path to the file.

        Returns:
            The stored hash string, or None if not tracked.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT hash FROM file_hashes WHERE file_path = ?", (file_path,)
            ).fetchone()
            return row["hash"] if row else None

    def set_file_hash(self, file_path: str, file_hash: str) -> None:
        """Store or update the hash for a file.

        Args:
            file_path: Path to the file.
            file_hash: SHA-256 hash of the file contents.
        """
        now = int(time.time())
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO file_hashes (file_path, hash, updated_at)
                   VALUES (?, ?, ?)""",
                (file_path, file_hash, now),
            )
            self.conn.commit()

    def delete_file_hash(self, file_path: str) -> None:
        """Remove a file hash entry.

        Args:
            file_path: Path to the file.
        """
        with self._lock:
            self.conn.execute("DELETE FROM file_hashes WHERE file_path = ?", (file_path,))
            self.conn.commit()

    # ── Deletion ──────────────────────────────────────────────────────────

    def delete_nodes_for_file(self, file_path: str) -> int:
        """Delete all nodes (and cascading edges) for a given file.

        Args:
            file_path: Path to the source file.

        Returns:
            Number of nodes deleted.
        """
        with self._lock:
            cursor = self.conn.execute(
                "DELETE FROM nodes WHERE file_path = ?", (file_path,)
            )
            self.conn.commit()
            return cursor.rowcount

    # ── Read operations ───────────────────────────────────────────────────

    def get_node(self, node_id: str) -> Optional[dict]:
        """Retrieve a single node by ID.

        Args:
            node_id: The node's hex ID.

        Returns:
            A dict of node fields, or None if not found.
        """
        with self._lock:
            row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            return dict(row) if row else None

    def search_nodes(self, query: str, kind: Optional[str] = None, limit: int = 20) -> list[dict]:
        """Search nodes by name (case-insensitive LIKE match).

        Args:
            query: Search term for the node name.
            kind: Optional NodeKind filter.
            limit: Maximum number of results.

        Returns:
            List of matching node dicts.
        """
        sql = "SELECT * FROM nodes WHERE name LIKE ?"
        params: list = [f"%{query}%"]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY name LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_edges_from(self, node_id: str) -> list[dict]:
        """Get all outgoing edges from a node.

        Args:
            node_id: The source node ID.

        Returns:
            List of edge dicts.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM edges WHERE from_id = ?", (node_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_edges_to(self, node_id: str) -> list[dict]:
        """Get all incoming edges to a node.

        Args:
            node_id: The target node ID.

        Returns:
            List of edge dicts.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM edges WHERE to_id = ?", (node_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_nodes(self) -> list[dict]:
        """Retrieve all nodes in the graph.

        Returns:
            List of all node dicts.
        """
        with self._lock:
            rows = self.conn.execute("SELECT * FROM nodes").fetchall()
            return [dict(r) for r in rows]

    def get_all_edges(self) -> list[dict]:
        """Retrieve all edges in the graph.

        Returns:
            List of all edge dicts.
        """
        with self._lock:
            rows = self.conn.execute("SELECT * FROM edges").fetchall()
            return [dict(r) for r in rows]

    def get_nodes_for_file(self, file_path: str) -> list[dict]:
        """Get all nodes belonging to a specific file.

        Args:
            file_path: Path to the source file.

        Returns:
            List of node dicts for that file.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM nodes WHERE file_path = ?", (file_path,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Meta ──────────────────────────────────────────────────────────────

    def set_meta(self, key: str, value: str) -> None:
        """Set a metadata key-value pair.

        Args:
            key: Metadata key.
            value: Metadata value.
        """
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )
            self.conn.commit()

    def get_meta(self, key: str) -> Optional[str]:
        """Get a metadata value by key.

        Args:
            key: Metadata key.

        Returns:
            The value string, or None if not found.
        """
        with self._lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return summary statistics about the graph.

        Returns:
            Dict with node_count, edge_count, languages, and file_count.
        """
        with self._lock:
            node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            languages = [
                row[0]
                for row in self.conn.execute(
                    "SELECT DISTINCT language FROM nodes WHERE language != ''"
                ).fetchall()
            ]
            file_count = self.conn.execute(
                "SELECT COUNT(DISTINCT file_path) FROM nodes"
            ).fetchone()[0]
            return {
                "node_count": node_count,
                "edge_count": edge_count,
                "languages": sorted(languages),
                "file_count": file_count,
            }
