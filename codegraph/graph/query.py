"""Graph traversal and analysis queries.

Provides callers/callees lookup, impact analysis, and dead code detection.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

from codegraph.graph.store import GraphStore


class QueryEngine:
    """Query engine for traversing and analyzing the code graph."""

    def __init__(self, store: GraphStore) -> None:
        """Initialize the query engine.

        Args:
            store: The GraphStore to query.
        """
        self.store = store

    def get_callers(self, node_id: str) -> list[dict]:
        """Get all nodes that call the given node.

        Args:
            node_id: Target node ID.

        Returns:
            List of caller node dicts with edge info.
        """
        edges = self.store.get_edges_to(node_id)
        call_edges = [e for e in edges if e["kind"] == "CALLS"]
        result = []
        for edge in call_edges:
            node = self.store.get_node(edge["from_id"])
            if node:
                node["edge_confidence"] = edge.get("confidence", 1.0)
                result.append(node)
        return result

    def get_callees(self, node_id: str) -> list[dict]:
        """Get all nodes that the given node calls.

        Args:
            node_id: Source node ID.

        Returns:
            List of callee node dicts with edge info.
        """
        edges = self.store.get_edges_from(node_id)
        call_edges = [e for e in edges if e["kind"] == "CALLS"]
        result = []
        for edge in call_edges:
            node = self.store.get_node(edge["to_id"])
            if node:
                node["edge_confidence"] = edge.get("confidence", 1.0)
                result.append(node)
        return result

    def get_dependents(self, node_id: str) -> list[dict]:
        """Get all nodes that directly depend on the given node (incoming edges).

        Args:
            node_id: Target node ID.

        Returns:
            List of dependent node dicts.
        """
        edges = self.store.get_edges_to(node_id)
        result = []
        seen = set()
        for edge in edges:
            nid = edge["from_id"]
            if nid not in seen:
                seen.add(nid)
                node = self.store.get_node(nid)
                if node:
                    result.append(node)
        return result

    def get_dependencies(self, node_id: str) -> list[dict]:
        """Get all nodes that the given node depends on (outgoing edges).

        Args:
            node_id: Source node ID.

        Returns:
            List of dependency node dicts.
        """
        edges = self.store.get_edges_from(node_id)
        result = []
        seen = set()
        for edge in edges:
            nid = edge["to_id"]
            if nid not in seen:
                seen.add(nid)
                node = self.store.get_node(nid)
                if node:
                    result.append(node)
        return result

    def impact_analysis(self, node_id: str, max_depth: int = 10) -> list[dict]:
        """Transitive impact analysis — all nodes that depend on this node.

        BFS traversal following incoming edges to find all nodes that would
        be affected if the given node changes.

        Args:
            node_id: The node to analyze impact for.
            max_depth: Maximum traversal depth.

        Returns:
            List of impacted node dicts, ordered by distance.
        """
        visited: set[str] = {node_id}
        result: list[dict] = []
        q: deque[tuple[str, int]] = deque([(node_id, 0)])

        while q:
            current_id, depth = q.popleft()
            if depth >= max_depth:
                continue

            edges = self.store.get_edges_to(current_id)
            for edge in edges:
                dep_id = edge["from_id"]
                if dep_id not in visited:
                    visited.add(dep_id)
                    node = self.store.get_node(dep_id)
                    if node:
                        node["impact_depth"] = depth + 1
                        node["impact_edge_kind"] = edge["kind"]
                        result.append(node)
                        q.append((dep_id, depth + 1))

        return result

    def dead_code(self, exclude_kinds: Optional[list[str]] = None) -> list[dict]:
        """Find nodes with no incoming edges (potential dead code).

        Excludes FILE nodes and entry points by default.

        Args:
            exclude_kinds: Node kinds to exclude (default: FILE, MODULE, IMPORT).

        Returns:
            List of potentially dead code node dicts.
        """
        if exclude_kinds is None:
            exclude_kinds = ["FILE", "MODULE", "IMPORT"]

        all_nodes = self.store.get_all_nodes()
        result = []
        for node in all_nodes:
            if node["kind"] in exclude_kinds:
                continue
            incoming = self.store.get_edges_to(node["id"])
            # Filter to non-DEFINES edges (DEFINES from parent doesn't count)
            usage_edges = [e for e in incoming if e["kind"] != "DEFINES"]
            if not usage_edges:
                result.append(node)
        return result

    def find_node_by_name(self, name: str, kind: Optional[str] = None) -> Optional[dict]:
        """Find a single node by exact name match.

        Args:
            name: Exact name to search for.
            kind: Optional NodeKind filter.

        Returns:
            The matching node dict, or None.
        """
        results = self.store.search_nodes(name, kind=kind, limit=50)
        # Prefer exact match
        for r in results:
            if r["name"] == name:
                if kind is None or r["kind"] == kind:
                    return r
        return results[0] if results else None

    def get_siblings(self, node_id: str) -> list[dict]:
        """Get other nodes in the same file as the given node.

        Args:
            node_id: The reference node ID.

        Returns:
            List of sibling node dicts (excluding the node itself).
        """
        node = self.store.get_node(node_id)
        if not node:
            return []
        file_nodes = self.store.get_nodes_for_file(node["file_path"])
        return [n for n in file_nodes if n["id"] != node_id]

    def get_two_hop_neighbors(self, node_id: str, limit: int = 20) -> list[dict]:
        """Get nodes within 2 hops of the given node.

        Args:
            node_id: The center node ID.
            limit: Maximum number of results.

        Returns:
            List of neighbor node dicts.
        """
        visited: set[str] = {node_id}
        result: list[dict] = []

        # 1-hop: outgoing and incoming
        for edge in self.store.get_edges_from(node_id) + self.store.get_edges_to(node_id):
            neighbor_id = edge["to_id"] if edge["from_id"] == node_id else edge["from_id"]
            if neighbor_id not in visited:
                visited.add(neighbor_id)
                node = self.store.get_node(neighbor_id)
                if node:
                    result.append(node)

        # 2-hop
        hop1_ids = list(visited - {node_id})
        for h1_id in hop1_ids:
            if len(result) >= limit:
                break
            for edge in self.store.get_edges_from(h1_id) + self.store.get_edges_to(h1_id):
                if len(result) >= limit:
                    break
                nid = edge["to_id"] if edge["from_id"] == h1_id else edge["from_id"]
                if nid not in visited:
                    visited.add(nid)
                    node = self.store.get_node(nid)
                    if node:
                        result.append(node)

        return result[:limit]
