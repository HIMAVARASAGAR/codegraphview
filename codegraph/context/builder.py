"""AI context snapshot builder.

Takes a focal point (function, class, or file) and produces a token-budget-aware
JSON snapshot that AI systems can consume efficiently.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from codegraph.graph.store import GraphStore
from codegraph.graph.query import QueryEngine


# Rough token estimates (1 token ≈ 4 chars)
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return len(text) // _CHARS_PER_TOKEN


def _node_summary(node: dict) -> dict:
    """Create a compact summary of a node for context output."""
    return {
        "id": node["id"],
        "name": node["name"],
        "file": node["file_path"],
        "summary": node.get("ai_summary"),
    }


def _node_detail(node: dict) -> dict:
    """Create a detailed view of a node for focal context."""
    return {
        "id": node["id"],
        "kind": node["kind"],
        "name": node["name"],
        "file": node["file_path"],
        "signature": node.get("signature"),
        "summary": node.get("ai_summary"),
        "lines": [node.get("line_start"), node.get("line_end")],
        "complexity": node.get("complexity"),
    }


class ContextBuilder:
    """Builds token-budget-aware context snapshots for AI consumption.

    Budget allocation order:
    1. Focal node full detail (~400 tokens)
    2. Direct callers, up to 10 (~800 tokens)
    3. Direct callees, up to 10 (~800 tokens)
    4. File siblings, summarized (~400 tokens)
    5. Impact list, names only (~200 tokens)
    6. 2-hop neighbors, names + file only (remaining budget)
    """

    def __init__(self, store: GraphStore) -> None:
        """Initialize the context builder.

        Args:
            store: The GraphStore to build context from.
        """
        self.store = store
        self.query = QueryEngine(store)

    def build(self, focal: str, budget_tokens: int = 4000) -> dict:
        """Build a context snapshot centered on a focal point.

        Args:
            focal: Function name, class name, or file path.
            budget_tokens: Maximum token budget (default: 4000).

        Returns:
            A context snapshot dict ready for AI consumption.
        """
        budget_chars = budget_tokens * _CHARS_PER_TOKEN
        used_chars = 0

        # Resolve focal node
        focal_node = self._resolve_focal(focal)
        if focal_node is None:
            return {
                "error": f"Node not found: {focal}",
                "meta": self._meta(0, budget_tokens),
            }

        result: dict = {}

        # 1. Focal node detail (~400 tokens / ~1600 chars)
        focal_detail = _node_detail(focal_node)
        focal_json = json.dumps(focal_detail)
        used_chars += len(focal_json)
        result["focal"] = focal_detail

        node_id = focal_node["id"]

        # 2. Direct callers (~800 tokens)
        callers = self.query.get_callers(node_id)[:10]
        callers_data = [_node_summary(c) for c in callers]
        callers_json = json.dumps(callers_data)
        if used_chars + len(callers_json) <= budget_chars:
            used_chars += len(callers_json)
            result["called_by"] = callers_data
        else:
            result["called_by"] = []

        # 3. Direct callees (~800 tokens)
        callees = self.query.get_callees(node_id)[:10]
        callees_data = []
        for c in callees:
            entry = _node_summary(c)
            entry["confidence"] = c.get("edge_confidence", 1.0)
            callees_data.append(entry)
        callees_json = json.dumps(callees_data)
        if used_chars + len(callees_json) <= budget_chars:
            used_chars += len(callees_json)
            result["calls"] = callees_data
        else:
            result["calls"] = []

        # 4. Siblings (~400 tokens)
        siblings = self.query.get_siblings(node_id)
        siblings_data = [{"name": s["name"], "kind": s["kind"]}
                        for s in siblings[:10]]
        siblings_json = json.dumps(siblings_data)
        if used_chars + len(siblings_json) <= budget_chars:
            used_chars += len(siblings_json)
            result["siblings"] = siblings_data
        else:
            result["siblings"] = []

        # 5. Impact list (~200 tokens)
        impact = self.query.impact_analysis(node_id, max_depth=5)
        impact_names = [n["name"] for n in impact[:20]]
        impact_json = json.dumps(impact_names)
        if used_chars + len(impact_json) <= budget_chars:
            used_chars += len(impact_json)
            result["impact_if_changed"] = impact_names
        else:
            result["impact_if_changed"] = []

        # 6. 2-hop neighbors (remaining budget)
        remaining = budget_chars - used_chars
        if remaining > 200:
            neighbors = self.query.get_two_hop_neighbors(node_id, limit=20)
            neighbors_data = [{"name": n["name"], "file": n["file_path"]}
                            for n in neighbors]
            n_json = json.dumps(neighbors_data)
            if len(n_json) <= remaining:
                used_chars += len(n_json)
                result["neighbors_2hop"] = neighbors_data
            else:
                # Truncate to fit
                truncated = []
                for nd in neighbors_data:
                    test = json.dumps(truncated + [nd])
                    if len(test) <= remaining:
                        truncated.append(nd)
                    else:
                        break
                used_chars += len(json.dumps(truncated))
                result["neighbors_2hop"] = truncated

        # Meta
        token_estimate = used_chars // _CHARS_PER_TOKEN
        result["meta"] = self._meta(token_estimate, budget_tokens)

        return result

    def _resolve_focal(self, focal: str) -> Optional[dict]:
        """Resolve a focal identifier to a node.

        Tries exact name match, then file path match.
        """
        # Try by name (function or class)
        node = self.query.find_node_by_name(focal, kind="FUNCTION")
        if node:
            return node
        node = self.query.find_node_by_name(focal, kind="CLASS")
        if node:
            return node
        # Try as file path
        node = self.query.find_node_by_name(focal, kind="FILE")
        if node:
            return node
        # Try general search
        results = self.store.search_nodes(focal, limit=1)
        return results[0] if results else None

    def _meta(self, token_estimate: int, budget: int) -> dict:
        """Build the meta section of the context snapshot."""
        return {
            "graph_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "token_estimate": token_estimate,
            "budget_used_pct": round(
                (token_estimate / budget * 100) if budget > 0 else 0, 1
            ),
        }
