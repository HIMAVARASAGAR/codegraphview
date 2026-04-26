"""Tests for the AI context builder."""
from __future__ import annotations

import json
import pytest

from codegraph.parsers.base import NodeEvent, EdgeEvent, make_node_id
from codegraph.graph.store import GraphStore
from codegraph.context.builder import ContextBuilder


@pytest.fixture
def store_with_context(tmp_path):
    """Create a store with data suitable for context testing."""
    db_path = tmp_path / "codegraph" / "graph.db"
    store = GraphStore(db_path)
    store.connect()

    nodes = [
        NodeEvent(kind="FILE", name="processor.py", file_path="processor.py",
                 line_start=1, line_end=100, language="python"),
        NodeEvent(kind="FUNCTION", name="processPayment", file_path="processor.py",
                 line_start=10, line_end=30, signature="def processPayment(amount, card_id)",
                 language="python"),
        NodeEvent(kind="FUNCTION", name="validateCard", file_path="processor.py",
                 line_start=35, line_end=50, signature="def validateCard(card_id)",
                 language="python"),
        NodeEvent(kind="FUNCTION", name="checkoutHandler", file_path="handler.py",
                 line_start=5, line_end=25, signature="def checkoutHandler(request)",
                 language="python"),
        NodeEvent(kind="FILE", name="handler.py", file_path="handler.py",
                 line_start=1, line_end=50, language="python"),
    ]
    store.insert_nodes(nodes)

    pp_id = make_node_id("processor.py", "processPayment", "FUNCTION")
    vc_id = make_node_id("processor.py", "validateCard", "FUNCTION")
    ch_id = make_node_id("handler.py", "checkoutHandler", "FUNCTION")
    file_id = make_node_id("processor.py", "processor.py", "FILE")

    edges = [
        EdgeEvent(kind="DEFINES", from_id=file_id, to_id=pp_id, line=10),
        EdgeEvent(kind="DEFINES", from_id=file_id, to_id=vc_id, line=35),
        EdgeEvent(kind="CALLS", from_id=ch_id, to_id=pp_id, line=15),
        EdgeEvent(kind="CALLS", from_id=pp_id, to_id=vc_id, line=20),
    ]
    store.insert_edges(edges)

    yield store
    store.close()


class TestContextBuilder:
    """Tests for the AI context snapshot builder."""

    def test_build_basic(self, store_with_context):
        """Builder produces a valid context snapshot."""
        builder = ContextBuilder(store_with_context)
        ctx = builder.build("processPayment")
        assert "focal" in ctx
        assert ctx["focal"]["name"] == "processPayment"
        assert "meta" in ctx

    def test_build_not_found(self, store_with_context):
        """Builder handles missing focal nodes."""
        builder = ContextBuilder(store_with_context)
        ctx = builder.build("nonExistentFunction")
        assert "error" in ctx

    def test_build_callers(self, store_with_context):
        """Builder includes callers in context."""
        builder = ContextBuilder(store_with_context)
        ctx = builder.build("processPayment")
        assert "called_by" in ctx
        caller_names = [c["name"] for c in ctx["called_by"]]
        assert "checkoutHandler" in caller_names

    def test_build_callees(self, store_with_context):
        """Builder includes callees in context."""
        builder = ContextBuilder(store_with_context)
        ctx = builder.build("processPayment")
        assert "calls" in ctx
        callee_names = [c["name"] for c in ctx["calls"]]
        assert "validateCard" in callee_names

    def test_budget_respected(self, store_with_context):
        """Builder respects the token budget."""
        builder = ContextBuilder(store_with_context)
        ctx = builder.build("processPayment", budget_tokens=4000)
        assert ctx["meta"]["budget_used_pct"] <= 100

    def test_output_is_json_serializable(self, store_with_context):
        """Builder output is fully JSON-serializable."""
        builder = ContextBuilder(store_with_context)
        ctx = builder.build("processPayment")
        # Should not raise
        json.dumps(ctx)
