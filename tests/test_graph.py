"""Tests for CodeGraph graph store and query engine."""
from __future__ import annotations

import tempfile
import os
import json
import pytest

from codegraph.parsers.base import NodeEvent, EdgeEvent, make_node_id
from codegraph.graph.store import GraphStore
from codegraph.graph.query import QueryEngine
from codegraph.graph.exporter import Exporter


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary graph store."""
    db_path = tmp_path / "codegraph" / "graph.db"
    store = GraphStore(db_path)
    store.connect()
    yield store
    store.close()


@pytest.fixture
def populated_store(tmp_db):
    """Create a store with sample data."""
    store = tmp_db

    # Insert some nodes
    nodes = [
        NodeEvent(kind="FILE", name="app.py", file_path="app.py",
                 line_start=1, line_end=50, language="python"),
        NodeEvent(kind="FUNCTION", name="main", file_path="app.py",
                 line_start=5, line_end=20, signature="def main()",
                 language="python", is_exported=True),
        NodeEvent(kind="FUNCTION", name="helper", file_path="app.py",
                 line_start=25, line_end=35, signature="def helper(x)",
                 language="python"),
        NodeEvent(kind="CLASS", name="App", file_path="app.py",
                 line_start=37, line_end=50, signature="class App",
                 language="python"),
    ]
    store.insert_nodes(nodes)

    # Insert edges
    file_id = make_node_id("app.py", "app.py", "FILE")
    main_id = make_node_id("app.py", "main", "FUNCTION")
    helper_id = make_node_id("app.py", "helper", "FUNCTION")
    app_id = make_node_id("app.py", "App", "CLASS")

    edges = [
        EdgeEvent(kind="DEFINES", from_id=file_id, to_id=main_id, line=5),
        EdgeEvent(kind="DEFINES", from_id=file_id, to_id=helper_id, line=25),
        EdgeEvent(kind="DEFINES", from_id=file_id, to_id=app_id, line=37),
        EdgeEvent(kind="CALLS", from_id=main_id, to_id=helper_id, line=10),
    ]
    store.insert_edges(edges)

    return store


# ── Store tests ───────────────────────────────────────────────────────

class TestGraphStore:
    """Tests for the SQLite graph store."""

    def test_create_and_connect(self, tmp_db):
        """Store creates database and schema."""
        stats = tmp_db.stats()
        assert stats["node_count"] == 0
        assert stats["edge_count"] == 0

    def test_insert_node(self, tmp_db):
        """Store inserts and retrieves a node."""
        event = NodeEvent(kind="FUNCTION", name="test_func", file_path="test.py",
                         line_start=1, line_end=5, language="python")
        node_id = tmp_db.insert_node(event)
        assert node_id
        node = tmp_db.get_node(node_id)
        assert node is not None
        assert node["name"] == "test_func"
        assert node["kind"] == "FUNCTION"

    def test_insert_nodes_batch(self, tmp_db):
        """Store inserts multiple nodes in a batch."""
        events = [
            NodeEvent(kind="FUNCTION", name=f"func_{i}", file_path="test.py",
                     line_start=i*10, line_end=i*10+5, language="python")
            for i in range(10)
        ]
        ids = tmp_db.insert_nodes(events)
        assert len(ids) == 10
        stats = tmp_db.stats()
        assert stats["node_count"] == 10

    def test_search_nodes(self, populated_store):
        """Store searches nodes by name."""
        results = populated_store.search_nodes("main")
        assert len(results) >= 1
        assert results[0]["name"] == "main"

    def test_search_with_kind_filter(self, populated_store):
        """Store filters search by kind."""
        results = populated_store.search_nodes("main", kind="FUNCTION")
        assert len(results) == 1
        results = populated_store.search_nodes("main", kind="CLASS")
        assert len(results) == 0

    def test_delete_nodes_for_file(self, populated_store):
        """Store deletes all nodes for a file."""
        count = populated_store.delete_nodes_for_file("app.py")
        assert count == 4
        stats = populated_store.stats()
        assert stats["node_count"] == 0

    def test_file_hash_operations(self, tmp_db):
        """Store tracks file hashes."""
        assert tmp_db.get_file_hash("test.py") is None
        tmp_db.set_file_hash("test.py", "abc123")
        assert tmp_db.get_file_hash("test.py") == "abc123"
        tmp_db.set_file_hash("test.py", "def456")
        assert tmp_db.get_file_hash("test.py") == "def456"
        tmp_db.delete_file_hash("test.py")
        assert tmp_db.get_file_hash("test.py") is None

    def test_meta_operations(self, tmp_db):
        """Store manages metadata key-value pairs."""
        assert tmp_db.get_meta("version") is None
        tmp_db.set_meta("version", "1.0")
        assert tmp_db.get_meta("version") == "1.0"

    def test_edges_from_to(self, populated_store):
        """Store retrieves edges by from/to node."""
        main_id = make_node_id("app.py", "main", "FUNCTION")
        helper_id = make_node_id("app.py", "helper", "FUNCTION")

        from_edges = populated_store.get_edges_from(main_id)
        calls = [e for e in from_edges if e["kind"] == "CALLS"]
        assert len(calls) == 1
        assert calls[0]["to_id"] == helper_id

        to_edges = populated_store.get_edges_to(helper_id)
        incoming_calls = [e for e in to_edges if e["kind"] == "CALLS"]
        assert len(incoming_calls) == 1

    def test_insert_edge_skips_unresolved(self, tmp_db):
        """Store skips edges where to_id doesn't exist."""
        edge = EdgeEvent(kind="CALLS", from_id="nonexistent1",
                        to_id="nonexistent2", line=1)
        result = tmp_db.insert_edge(edge)
        assert result == ""


# ── Query engine tests ────────────────────────────────────────────────

class TestQueryEngine:
    """Tests for the graph query engine."""

    def test_get_callers(self, populated_store):
        """Query engine finds callers."""
        engine = QueryEngine(populated_store)
        helper_id = make_node_id("app.py", "helper", "FUNCTION")
        callers = engine.get_callers(helper_id)
        assert len(callers) == 1
        assert callers[0]["name"] == "main"

    def test_get_callees(self, populated_store):
        """Query engine finds callees."""
        engine = QueryEngine(populated_store)
        main_id = make_node_id("app.py", "main", "FUNCTION")
        callees = engine.get_callees(main_id)
        assert len(callees) == 1
        assert callees[0]["name"] == "helper"

    def test_impact_analysis(self, populated_store):
        """Query engine performs transitive impact analysis."""
        engine = QueryEngine(populated_store)
        helper_id = make_node_id("app.py", "helper", "FUNCTION")
        impact = engine.impact_analysis(helper_id)
        names = [n["name"] for n in impact]
        assert "main" in names

    def test_dead_code(self, populated_store):
        """Query engine finds potentially dead code."""
        engine = QueryEngine(populated_store)
        dead = engine.dead_code()
        names = [n["name"] for n in dead]
        # main has no incoming CALLS (only DEFINES which is excluded)
        assert "main" in names

    def test_find_node_by_name(self, populated_store):
        """Query engine finds nodes by exact name."""
        engine = QueryEngine(populated_store)
        node = engine.find_node_by_name("helper")
        assert node is not None
        assert node["name"] == "helper"

    def test_get_siblings(self, populated_store):
        """Query engine finds sibling nodes in the same file."""
        engine = QueryEngine(populated_store)
        main_id = make_node_id("app.py", "main", "FUNCTION")
        siblings = engine.get_siblings(main_id)
        names = [s["name"] for s in siblings]
        assert "helper" in names
        assert "App" in names


# ── Exporter tests ────────────────────────────────────────────────────

class TestExporter:
    """Tests for the graph exporter."""

    def test_export_empty(self, tmp_db, tmp_path):
        """Exporter produces valid JSON even with empty graph."""
        export_path = tmp_path / "graph.json"
        exporter = Exporter(tmp_db, export_path)
        result = exporter.export()
        assert result.exists()
        data = json.loads(result.read_text())
        assert data["meta"]["node_count"] == 0
        assert data["meta"]["edge_count"] == 0
        assert data["nodes"] == {}
        assert data["edges"] == []

    def test_export_with_data(self, populated_store, tmp_path):
        """Exporter produces correct JSON with populated graph."""
        export_path = tmp_path / "graph.json"
        exporter = Exporter(populated_store, export_path)
        result = exporter.export()
        data = json.loads(result.read_text())
        assert data["meta"]["node_count"] == 4
        assert data["meta"]["edge_count"] == 4
        assert len(data["nodes"]) == 4
        assert len(data["edges"]) == 4
        assert "python" in data["meta"]["languages"]
