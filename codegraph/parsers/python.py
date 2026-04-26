"""Python language parser using tree-sitter.

Parses Python source code into CodeGraph IR (NodeEvents and EdgeEvents).
Handles: functions, classes, imports, variables, calls, inheritance,
decorators, async functions, and assignments.
"""

from __future__ import annotations

from typing import Optional

import tree_sitter_python as tspython
from tree_sitter import Language, Parser as TSParser, Node

from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id


PY_LANGUAGE = Language(tspython.language())


class PythonParser(Parser):
    """Tree-sitter based parser for Python source code."""

    language = "python"
    extensions = [".py"]

    def __init__(self) -> None:
        self._ts_parser = TSParser(PY_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse Python source code into IR events.

        Args:
            file_path: Path to the Python source file.
            content: Raw Python source code.

        Returns:
            Tuple of (node_events, edge_events).
        """
        tree = self._ts_parser.parse(content.encode("utf-8"))
        nodes: list[NodeEvent] = []
        edges: list[EdgeEvent] = []

        # Emit a FILE node
        file_node = NodeEvent(
            kind="FILE",
            name=file_path,
            file_path=file_path,
            line_start=1,
            line_end=content.count("\n") + 1,
            language=self.language,
            is_exported=True,
        )
        nodes.append(file_node)
        file_id = make_node_id(file_path, file_path, "FILE")

        self._walk(tree.root_node, file_path, file_id, None, nodes, edges, content)
        return nodes, edges

    def _walk(
        self,
        node: Node,
        file_path: str,
        file_id: str,
        parent_id: Optional[str],
        nodes: list[NodeEvent],
        edges: list[EdgeEvent],
        content: str,
    ) -> None:
        """Recursively walk the syntax tree and emit IR events.

        Args:
            node: Current tree-sitter node.
            file_path: Path to the source file.
            file_id: ID of the FILE node.
            parent_id: ID of the parent node (class/function), or None for module-level.
            nodes: Accumulator for NodeEvents.
            edges: Accumulator for EdgeEvents.
            content: Full source code text.
        """
        if node.type == "function_definition":
            self._handle_function(node, file_path, file_id, parent_id, nodes, edges, content)
        elif node.type == "class_definition":
            self._handle_class(node, file_path, file_id, parent_id, nodes, edges, content)
        elif node.type == "import_statement":
            self._handle_import(node, file_path, file_id, parent_id, nodes, edges)
        elif node.type == "import_from_statement":
            self._handle_import_from(node, file_path, file_id, parent_id, nodes, edges)
        elif node.type == "call":
            self._handle_call(node, file_path, parent_id or file_id, edges)
        elif node.type in ("assignment", "augmented_assignment"):
            self._handle_assignment(node, file_path, file_id, parent_id, nodes, edges)
        else:
            # Recurse into children
            for child in node.children:
                self._walk(child, file_path, file_id, parent_id, nodes, edges, content)

    def _handle_function(
        self,
        node: Node,
        file_path: str,
        file_id: str,
        parent_id: Optional[str],
        nodes: list[NodeEvent],
        edges: list[EdgeEvent],
        content: str,
    ) -> None:
        """Extract a function definition node."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        line_start = node.start_point[0] + 1
        line_end = node.end_point[0] + 1

        # Check if async
        is_async = False
        if node.parent and node.parent.type == "decorated_definition":
            # Check grandparent too
            pass
        # Check if the previous sibling or parent marks it async
        prev = node.prev_named_sibling
        if node.parent and node.parent.type == "decorated_definition":
            decorated = node.parent
            for child in decorated.children:
                if child.type == "async":
                    is_async = True
                    break
        # Simpler: check raw text
        func_text = content[node.start_byte:node.end_byte]
        # Also check parent for async keyword
        if node.parent:
            parent_start = node.parent.start_byte
            prefix = content[parent_start:node.start_byte].strip()
            if prefix.endswith("async"):
                is_async = True

        # Extract decorators
        decorators: list[str] = []
        if node.parent and node.parent.type == "decorated_definition":
            for child in node.parent.children:
                if child.type == "decorator":
                    dec_text = child.text.decode("utf-8").lstrip("@").strip()
                    decorators.append(dec_text)

        # Build signature
        params_node = node.child_by_field_name("parameters")
        return_type = node.child_by_field_name("return_type")
        sig = f"def {name}"
        if params_node:
            sig += params_node.text.decode("utf-8")
        if return_type:
            sig += f" -> {return_type.text.decode('utf-8')}"

        is_exported = not name.startswith("_")

        func_event = NodeEvent(
            kind="FUNCTION",
            name=name,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            signature=sig,
            parent_id=parent_id,
            language=self.language,
            is_async=is_async,
            is_exported=is_exported,
            decorators=decorators,
        )
        nodes.append(func_event)

        func_id = make_node_id(file_path, name, "FUNCTION")
        container_id = parent_id or file_id
        edges.append(EdgeEvent(
            kind="DEFINES",
            from_id=container_id,
            to_id=func_id,
            line=line_start,
        ))

        # Walk the function body for calls, nested defs, etc.
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(child, file_path, file_id, func_id, nodes, edges, content)

    def _handle_class(
        self,
        node: Node,
        file_path: str,
        file_id: str,
        parent_id: Optional[str],
        nodes: list[NodeEvent],
        edges: list[EdgeEvent],
        content: str,
    ) -> None:
        """Extract a class definition node with inheritance."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        name = name_node.text.decode("utf-8")
        line_start = node.start_point[0] + 1
        line_end = node.end_point[0] + 1

        # Extract decorators
        decorators: list[str] = []
        if node.parent and node.parent.type == "decorated_definition":
            for child in node.parent.children:
                if child.type == "decorator":
                    dec_text = child.text.decode("utf-8").lstrip("@").strip()
                    decorators.append(dec_text)

        # Build signature with bases
        superclasses_node = node.child_by_field_name("superclasses")
        sig = f"class {name}"
        base_names: list[str] = []
        if superclasses_node:
            sig += superclasses_node.text.decode("utf-8")
            # Extract individual base class names
            for child in superclasses_node.children:
                if child.type == "identifier":
                    base_names.append(child.text.decode("utf-8"))
                elif child.type == "attribute":
                    base_names.append(child.text.decode("utf-8"))

        is_exported = not name.startswith("_")

        class_event = NodeEvent(
            kind="CLASS",
            name=name,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            signature=sig,
            parent_id=parent_id,
            language=self.language,
            is_exported=is_exported,
            decorators=decorators,
        )
        nodes.append(class_event)

        class_id = make_node_id(file_path, name, "CLASS")
        container_id = parent_id or file_id
        edges.append(EdgeEvent(
            kind="DEFINES",
            from_id=container_id,
            to_id=class_id,
            line=line_start,
        ))

        # Emit INHERITS edges
        for base in base_names:
            base_id = make_node_id(file_path, base, "CLASS")
            edges.append(EdgeEvent(
                kind="INHERITS",
                from_id=class_id,
                to_id=base_id,
                line=line_start,
                confidence=0.8,  # base might be from another file
            ))

        # Walk the class body
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(child, file_path, file_id, class_id, nodes, edges, content)

    def _handle_import(
        self,
        node: Node,
        file_path: str,
        file_id: str,
        parent_id: Optional[str],
        nodes: list[NodeEvent],
        edges: list[EdgeEvent],
    ) -> None:
        """Extract a plain import statement (import foo, import foo.bar)."""
        for child in node.children:
            if child.type == "dotted_name":
                module_name = child.text.decode("utf-8")
                self._emit_import(module_name, file_path, file_id, parent_id, node, nodes, edges)
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node:
                    module_name = name_node.text.decode("utf-8")
                    self._emit_import(module_name, file_path, file_id, parent_id, node, nodes, edges)

    def _handle_import_from(
        self,
        node: Node,
        file_path: str,
        file_id: str,
        parent_id: Optional[str],
        nodes: list[NodeEvent],
        edges: list[EdgeEvent],
    ) -> None:
        """Extract a from ... import ... statement."""
        # Get the module name
        module_node = node.child_by_field_name("module_name")
        module_name = module_node.text.decode("utf-8") if module_node else ""

        # Get imported names
        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                imported = child.text.decode("utf-8")
                full = f"{module_name}.{imported}" if module_name else imported
                self._emit_import(full, file_path, file_id, parent_id, node, nodes, edges)
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node:
                    imported = name_node.text.decode("utf-8")
                    full = f"{module_name}.{imported}" if module_name else imported
                    self._emit_import(full, file_path, file_id, parent_id, node, nodes, edges)
            elif child.type == "import_prefix":
                continue  # relative import dots

    def _emit_import(
        self,
        module_name: str,
        file_path: str,
        file_id: str,
        parent_id: Optional[str],
        node: Node,
        nodes: list[NodeEvent],
        edges: list[EdgeEvent],
    ) -> None:
        """Emit an IMPORT node and IMPORTS edge."""
        line = node.start_point[0] + 1
        import_event = NodeEvent(
            kind="IMPORT",
            name=module_name,
            file_path=file_path,
            line_start=line,
            line_end=line,
            language=self.language,
        )
        nodes.append(import_event)

        import_id = make_node_id(file_path, module_name, "IMPORT")
        container_id = parent_id or file_id
        edges.append(EdgeEvent(
            kind="IMPORTS",
            from_id=container_id,
            to_id=import_id,
            line=line,
        ))

    def _handle_call(
        self,
        node: Node,
        file_path: str,
        caller_id: str,
        edges: list[EdgeEvent],
    ) -> None:
        """Extract a function call and emit a CALLS edge."""
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        callee_name = func_node.text.decode("utf-8")
        line = node.start_point[0] + 1

        # Try to resolve the callee ID — could be a function or class (instantiation)
        callee_func_id = make_node_id(file_path, callee_name, "FUNCTION")
        callee_class_id = make_node_id(file_path, callee_name, "CLASS")

        # Emit CALLS edge to function (with high confidence for simple names)
        confidence = 1.0 if "." not in callee_name else 0.7
        edges.append(EdgeEvent(
            kind="CALLS",
            from_id=caller_id,
            to_id=callee_func_id,
            line=line,
            confidence=confidence,
        ))

        # If it looks like a class instantiation (starts with uppercase), also emit INSTANTIATES
        base_name = callee_name.split(".")[-1]
        if base_name and base_name[0].isupper():
            edges.append(EdgeEvent(
                kind="INSTANTIATES",
                from_id=caller_id,
                to_id=callee_class_id,
                line=line,
                confidence=0.7,
            ))

        # Walk call arguments for nested calls
        args_node = node.child_by_field_name("arguments")
        if args_node:
            for child in args_node.children:
                self._walk_for_calls(child, file_path, caller_id, edges)

    def _walk_for_calls(
        self,
        node: Node,
        file_path: str,
        caller_id: str,
        edges: list[EdgeEvent],
    ) -> None:
        """Walk a subtree looking only for call expressions."""
        if node.type == "call":
            self._handle_call(node, file_path, caller_id, edges)
        else:
            for child in node.children:
                self._walk_for_calls(child, file_path, caller_id, edges)

    def _handle_assignment(
        self,
        node: Node,
        file_path: str,
        file_id: str,
        parent_id: Optional[str],
        nodes: list[NodeEvent],
        edges: list[EdgeEvent],
    ) -> None:
        """Extract module/class-level variable assignments."""
        # Only emit VARIABLE nodes for module-level or class-level assignments
        left = node.child_by_field_name("left")
        if left is None:
            return

        # Get variable name
        if left.type == "identifier":
            var_name = left.text.decode("utf-8")
        elif left.type == "pattern_list":
            # Multi-assignment — take the first name
            for child in left.children:
                if child.type == "identifier":
                    var_name = child.text.decode("utf-8")
                    break
            else:
                return
        else:
            return

        line = node.start_point[0] + 1
        is_exported = not var_name.startswith("_")

        var_event = NodeEvent(
            kind="VARIABLE",
            name=var_name,
            file_path=file_path,
            line_start=line,
            line_end=node.end_point[0] + 1,
            language=self.language,
            parent_id=parent_id,
            is_exported=is_exported,
        )
        nodes.append(var_event)

        var_id = make_node_id(file_path, var_name, "VARIABLE")
        container_id = parent_id or file_id
        edges.append(EdgeEvent(
            kind="DEFINES",
            from_id=container_id,
            to_id=var_id,
            line=line,
        ))

        # Walk the right side for calls
        right = node.child_by_field_name("right")
        if right:
            self._walk_for_calls(right, file_path, container_id, edges)
