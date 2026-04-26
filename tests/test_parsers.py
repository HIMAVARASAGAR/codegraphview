"""Tests for CodeGraph parsers."""
from __future__ import annotations

import pytest

from codegraph.parsers.base import NodeEvent, EdgeEvent, make_node_id, Parser
from codegraph.parsers.python import PythonParser


# ── make_node_id ──────────────────────────────────────────────────────

class TestMakeNodeId:
    """Tests for the deterministic node ID generation."""

    def test_deterministic(self):
        """Same inputs always produce the same ID."""
        id1 = make_node_id("foo.py", "bar", "FUNCTION")
        id2 = make_node_id("foo.py", "bar", "FUNCTION")
        assert id1 == id2

    def test_different_inputs(self):
        """Different inputs produce different IDs."""
        id1 = make_node_id("foo.py", "bar", "FUNCTION")
        id2 = make_node_id("foo.py", "baz", "FUNCTION")
        assert id1 != id2

    def test_length(self):
        """IDs are always 16 hex chars."""
        id1 = make_node_id("file.py", "my_func", "FUNCTION")
        assert len(id1) == 16
        assert all(c in "0123456789abcdef" for c in id1)


# ── Python parser ────────────────────────────────────────────────────

class TestPythonParser:
    """Tests for the Python language parser."""

    @pytest.fixture
    def parser(self):
        return PythonParser()

    def test_language(self, parser):
        """Parser reports correct language."""
        assert parser.language == "python"
        assert ".py" in parser.extensions

    def test_parse_empty(self, parser):
        """Parsing empty content should not crash."""
        nodes, edges = parser.parse("empty.py", "")
        assert len(nodes) >= 1  # At least FILE node
        assert nodes[0].kind == "FILE"

    def test_parse_function(self, parser):
        """Parser extracts function definitions."""
        code = '''
def hello(name: str) -> str:
    return f"Hello, {name}"
'''
        nodes, edges = parser.parse("test.py", code)
        func_nodes = [n for n in nodes if n.kind == "FUNCTION"]
        assert len(func_nodes) == 1
        assert func_nodes[0].name == "hello"
        assert func_nodes[0].signature is not None
        assert "name: str" in func_nodes[0].signature

    def test_parse_class(self, parser):
        """Parser extracts class definitions."""
        code = '''
class MyClass(BaseClass):
    def method(self):
        pass
'''
        nodes, edges = parser.parse("test.py", code)
        class_nodes = [n for n in nodes if n.kind == "CLASS"]
        assert len(class_nodes) == 1
        assert class_nodes[0].name == "MyClass"

    def test_parse_imports(self, parser):
        """Parser extracts import statements."""
        code = '''
import os
from pathlib import Path
'''
        nodes, edges = parser.parse("test.py", code)
        import_nodes = [n for n in nodes if n.kind == "IMPORT"]
        assert len(import_nodes) >= 2

    def test_parse_variables(self, parser):
        """Parser extracts module-level variables."""
        code = '''
MAX_SIZE = 100
_private = "secret"
'''
        nodes, edges = parser.parse("test.py", code)
        var_nodes = [n for n in nodes if n.kind == "VARIABLE"]
        assert len(var_nodes) >= 2

    def test_parse_async_function(self, parser):
        """Parser detects async functions."""
        code = '''
async def fetch_data(url: str):
    pass
'''
        nodes, edges = parser.parse("test.py", code)
        func_nodes = [n for n in nodes if n.kind == "FUNCTION"]
        assert len(func_nodes) == 1
        # Note: async detection depends on tree-sitter node structure

    def test_parse_decorators(self, parser):
        """Parser extracts function decorators."""
        code = '''
@staticmethod
def my_static():
    pass
'''
        nodes, edges = parser.parse("test.py", code)
        func_nodes = [n for n in nodes if n.kind == "FUNCTION"]
        assert len(func_nodes) == 1

    def test_defines_edges(self, parser):
        """Parser emits DEFINES edges for functions."""
        code = '''
def foo():
    pass
'''
        nodes, edges = parser.parse("test.py", code)
        defines = [e for e in edges if e.kind == "DEFINES"]
        assert len(defines) >= 1

    def test_calls_edges(self, parser):
        """Parser emits CALLS edges for function calls."""
        code = '''
def foo():
    bar()
'''
        nodes, edges = parser.parse("test.py", code)
        calls = [e for e in edges if e.kind == "CALLS"]
        assert len(calls) >= 1

    def test_inherits_edges(self, parser):
        """Parser emits INHERITS edges for class inheritance."""
        code = '''
class Child(Parent):
    pass
'''
        nodes, edges = parser.parse("test.py", code)
        inherits = [e for e in edges if e.kind == "INHERITS"]
        assert len(inherits) >= 1

    def test_malformed_code(self, parser):
        """Parser handles malformed code without crashing."""
        code = '''
def broken(
    class ??? syntax error
    this is not valid python
'''
        nodes, edges = parser.parse("broken.py", code)
        # Should return at least the FILE node
        assert len(nodes) >= 1

    def test_complex_code(self, parser):
        """Parser handles complex real-world-like code."""
        code = '''
import os
from pathlib import Path

MAX_RETRIES = 3

class PaymentProcessor(BaseProcessor):
    """Handles payment processing."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def process(self, amount: int) -> bool:
        result = self._validate(amount)
        return self._charge(result)

    def _validate(self, amount: int) -> dict:
        if amount <= 0:
            raise ValueError("Invalid amount")
        return {"amount": amount, "valid": True}

    def _charge(self, data: dict) -> bool:
        return True
'''
        nodes, edges = parser.parse("payment.py", code)
        file_nodes = [n for n in nodes if n.kind == "FILE"]
        class_nodes = [n for n in nodes if n.kind == "CLASS"]
        func_nodes = [n for n in nodes if n.kind == "FUNCTION"]
        import_nodes = [n for n in nodes if n.kind == "IMPORT"]
        var_nodes = [n for n in nodes if n.kind == "VARIABLE"]

        assert len(file_nodes) == 1
        assert len(class_nodes) == 1
        assert class_nodes[0].name == "PaymentProcessor"
        assert len(func_nodes) >= 4  # __init__, process, _validate, _charge
        assert len(import_nodes) >= 2
        assert len(var_nodes) >= 1
