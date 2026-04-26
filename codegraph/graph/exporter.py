"""Graph exporter — SQLite → graph.json.

Exports the graph database to a JSON file that AI systems can read.
Handles sharding for large repos (>500k nodes).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from codegraph.graph.store import GraphStore

logger = logging.getLogger(__name__)

# Threshold for sharding
_SHARD_THRESHOLD = 500_000


class Exporter:
    """Exports the SQLite graph to JSON format.

    Produces graph.json with nodes, edges, and metadata.
    For repos with >500k nodes, shards into separate files.
    """

    def __init__(self, store: GraphStore, output_path: str | Path) -> None:
        """Initialize the exporter.

        Args:
            store: The GraphStore to export from.
            output_path: Path for the output graph.json file.
        """
        self.store = store
        self.output_path = Path(output_path)
        self._lock = threading.Lock()

    def export(self) -> Path:
        """Export the full graph to JSON.

        Returns:
            Path to the exported file.
        """
        with self._lock:
            stats = self.store.stats()
            if stats["node_count"] > _SHARD_THRESHOLD:
                return self._export_sharded(stats)
            return self._export_single(stats)

    def _export_single(self, stats: dict) -> Path:
        """Export the graph as a single JSON file."""
        all_nodes = self.store.get_all_nodes()
        all_edges = self.store.get_all_edges()

        nodes_dict = {}
        for n in all_nodes:
            nodes_dict[n["id"]] = {
                "kind": n["kind"],
                "name": n["name"],
                "file": n["file_path"],
                "lines": [n["line_start"], n["line_end"]],
                "signature": n.get("signature"),
                "language": n.get("language", ""),
                "ai_summary": n.get("ai_summary"),
                "complexity": n.get("complexity"),
                "is_async": bool(n.get("is_async", 0)),
                "is_exported": bool(n.get("is_exported", 0)),
            }

        edges_list = []
        for e in all_edges:
            edges_list.append({
                "from": e["from_id"],
                "to": e["to_id"],
                "kind": e["kind"],
                "line": e.get("line"),
                "confidence": e.get("confidence", 1.0),
            })

        graph = {
            "meta": {
                "version": "1.0",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "node_count": stats["node_count"],
                "edge_count": stats["edge_count"],
                "languages": stats["languages"],
                "schema": "https://codegraph.dev/schema/v1",
            },
            "nodes": nodes_dict,
            "edges": edges_list,
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
        logger.info("Exported graph.json: %d nodes, %d edges", stats["node_count"], stats["edge_count"])
        return self.output_path

    def _export_sharded(self, stats: dict) -> Path:
        """Export the graph as sharded JSON files for large repos."""
        base = self.output_path.parent
        base.mkdir(parents=True, exist_ok=True)

        all_nodes = self.store.get_all_nodes()
        all_edges = self.store.get_all_edges()

        # Build nodes dict
        nodes_dict = {}
        file_index: dict[str, list[str]] = {}
        for n in all_nodes:
            nid = n["id"]
            nodes_dict[nid] = {
                "kind": n["kind"],
                "name": n["name"],
                "file": n["file_path"],
                "lines": [n["line_start"], n["line_end"]],
                "signature": n.get("signature"),
                "language": n.get("language", ""),
                "ai_summary": n.get("ai_summary"),
                "complexity": n.get("complexity"),
                "is_async": bool(n.get("is_async", 0)),
                "is_exported": bool(n.get("is_exported", 0)),
            }
            fp = n["file_path"]
            if fp not in file_index:
                file_index[fp] = []
            file_index[fp].append(nid)

        # Edges list
        edges_list = [
            {
                "from": e["from_id"],
                "to": e["to_id"],
                "kind": e["kind"],
                "line": e.get("line"),
                "confidence": e.get("confidence", 1.0),
            }
            for e in all_edges
        ]

        # Write shards
        nodes_path = base / "graph.nodes.json"
        edges_path = base / "graph.edges.json"
        index_path = base / "graph.index.json"

        nodes_path.write_text(json.dumps(nodes_dict, indent=2), encoding="utf-8")
        edges_path.write_text(json.dumps(edges_list, indent=2), encoding="utf-8")
        index_path.write_text(json.dumps({
            "meta": {
                "version": "1.0",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "node_count": stats["node_count"],
                "edge_count": stats["edge_count"],
                "languages": stats["languages"],
                "schema": "https://codegraph.dev/schema/v1",
                "sharded": True,
            },
            "files": file_index,
        }, indent=2), encoding="utf-8")

        logger.info("Exported sharded graph: %d nodes, %d edges", stats["node_count"], stats["edge_count"])
        return index_path
