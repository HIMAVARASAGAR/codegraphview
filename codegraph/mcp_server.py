"""MCP server for CodeGraph.

Exposes three tools via the Model Context Protocol:
- get_context: AI-ready context snapshot for a focal point
- search_graph: Search nodes by name with optional kind filter
- get_impact: Transitive impact analysis for a node
"""

from __future__ import annotations

import json
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from codegraph.graph.store import GraphStore
from codegraph.graph.query import QueryEngine
from codegraph.context.builder import ContextBuilder


def create_server(store: GraphStore) -> FastMCP:
    """Create and configure the MCP server.

    Args:
        store: The GraphStore to serve queries from.

    Returns:
        A configured FastMCP server instance.
    """
    mcp = FastMCP("codegraph")
    query_engine = QueryEngine(store)
    context_builder = ContextBuilder(store)

    @mcp.tool()
    def get_context(focal: str, budget_tokens: int = 4000) -> dict:
        """Get AI-ready context snapshot for a function, class, or file.

        Args:
            focal: function name, class name, or file path
            budget_tokens: Maximum token budget for the snapshot

        Returns:
            Context snapshot dict with focal details, callers, callees, etc.
        """
        return context_builder.build(focal, budget_tokens=budget_tokens)

    @mcp.tool()
    def search_graph(query: str, kind: str = None, limit: int = 20) -> list[dict]:
        """Search nodes by name. kind filters to FUNCTION, CLASS, FILE, etc.

        Args:
            query: Search term for the node name
            kind: Optional filter by node kind
            limit: Maximum number of results

        Returns:
            List of nodes with name, file, summary, id.
        """
        results = store.search_nodes(query, kind=kind, limit=limit)
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "kind": r["kind"],
                "file": r["file_path"],
                "signature": r.get("signature"),
                "summary": r.get("ai_summary"),
            }
            for r in results
        ]

    @mcp.tool()
    def get_impact(node_id: str) -> dict:
        """Returns all nodes that directly or transitively depend on this node.

        Used for impact analysis before making a change.

        Args:
            node_id: The ID of the node to analyze

        Returns:
            Dict with the focal node info and list of impacted nodes.
        """
        node = store.get_node(node_id)
        if not node:
            return {"error": f"Node not found: {node_id}"}

        impacted = query_engine.impact_analysis(node_id)
        return {
            "node": {
                "id": node["id"],
                "name": node["name"],
                "kind": node["kind"],
                "file": node["file_path"],
            },
            "impacted": [
                {
                    "id": n["id"],
                    "name": n["name"],
                    "kind": n["kind"],
                    "file": n["file_path"],
                    "depth": n.get("impact_depth", 0),
                }
                for n in impacted
            ],
            "total_impacted": len(impacted),
        }

    return mcp
