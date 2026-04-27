"""JavaScript language parser using tree-sitter."""

from __future__ import annotations
from typing import Optional

import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser as TSParser, Node

from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

JS_LANGUAGE = Language(tsjs.language())


class JavaScriptParser(Parser):
    """Tree-sitter based parser for JavaScript source code."""

    language = "javascript"
    extensions = [".js", ".jsx", ".mjs", ".cjs"]

    def __init__(self) -> None:
        self._ts = TSParser(JS_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse JavaScript source into IR events."""
        tree = self._ts.parse(content.encode("utf-8"))
        nodes: list[NodeEvent] = []
        edges: list[EdgeEvent] = []

        file_node = NodeEvent(
            kind="FILE", name=file_path, file_path=file_path,
            line_start=1, line_end=content.count("\n") + 1,
            language=self.language, is_exported=True,
        )
        nodes.append(file_node)
        file_id = make_node_id(file_path, file_path, "FILE")
        self._walk(tree.root_node, file_path, file_id, None, nodes, edges)
        return nodes, edges

    def _walk(self, node: Node, fp: str, fid: str, pid: Optional[str],
              nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        """Recursively walk the syntax tree."""
        t = node.type

        if t in ("function_declaration", "generator_function_declaration"):
            self._handle_func(node, fp, fid, pid, nodes, edges, is_arrow=False)
        elif t == "class_declaration":
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t == "import_statement":
            self._handle_import(node, fp, fid, pid, nodes, edges)
        elif t in ("variable_declaration", "lexical_declaration"):
            self._handle_var_decl(node, fp, fid, pid, nodes, edges)
        elif t == "call_expression":
            self._handle_call(node, fp, pid or fid, edges)
        elif t in ("jsx_self_closing_element", "jsx_opening_element"):
            self._handle_jsx_tag(node, fp, pid or fid, edges)
        elif t == "jsx_element":
            # Process the opening tag for CALLS, then recurse into children
            self._handle_jsx_element(node, fp, fid, pid, nodes, edges)
            return  # _handle_jsx_element already recurses
        elif t == "export_statement":
            for child in node.children:
                self._walk(child, fp, fid, pid, nodes, edges)
        else:
            for child in node.children:
                self._walk(child, fp, fid, pid, nodes, edges)

    def _handle_func(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent], is_arrow: bool = False) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        line_s = node.start_point[0] + 1
        line_e = node.end_point[0] + 1
        is_async = any(c.type == "async" for c in node.children)
        params = node.child_by_field_name("parameters")
        sig = f"function {name}" + (params.text.decode("utf-8") if params else "()")

        nodes.append(NodeEvent(
            kind="FUNCTION", name=name, file_path=fp,
            line_start=line_s, line_end=line_e, signature=sig,
            parent_id=pid, language=self.language, is_async=is_async,
            is_exported=not name.startswith("_"),
        ))
        func_id = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=func_id, line=line_s))

        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(child, fp, fid, func_id, nodes, edges)

    def _handle_class(self, node: Node, fp: str, fid: str, pid: Optional[str],
                      nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8")
        line_s = node.start_point[0] + 1
        line_e = node.end_point[0] + 1

        nodes.append(NodeEvent(
            kind="CLASS", name=name, file_path=fp,
            line_start=line_s, line_end=line_e,
            signature=f"class {name}", parent_id=pid,
            language=self.language, is_exported=True,
        ))
        class_id = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=class_id, line=line_s))

        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "method_definition":
                    mname_node = child.child_by_field_name("name")
                    if mname_node:
                        mname = mname_node.text.decode("utf-8")
                        mparams = child.child_by_field_name("parameters")
                        msig = f"{mname}" + (mparams.text.decode("utf-8") if mparams else "()")
                        nodes.append(NodeEvent(
                            kind="FUNCTION", name=mname, file_path=fp,
                            line_start=child.start_point[0]+1, line_end=child.end_point[0]+1,
                            signature=msig, parent_id=class_id, language=self.language,
                        ))
                        mid = make_node_id(fp, mname, "FUNCTION")
                        edges.append(EdgeEvent(kind="DEFINES", from_id=class_id, to_id=mid,
                                             line=child.start_point[0]+1))
                else:
                    self._walk(child, fp, fid, class_id, nodes, edges)

    def _handle_import(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        source = node.child_by_field_name("source")
        if source:
            mod = source.text.decode("utf-8").strip("'\"")
            line = node.start_point[0] + 1
            nodes.append(NodeEvent(
                kind="IMPORT", name=mod, file_path=fp,
                line_start=line, line_end=line, language=self.language,
            ))
            imp_id = make_node_id(fp, mod, "IMPORT")
            edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=imp_id, line=line))

    def _handle_var_decl(self, node: Node, fp: str, fid: str, pid: Optional[str],
                         nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if name_node and name_node.type == "identifier":
                    vname = name_node.text.decode("utf-8")
                    line = child.start_point[0] + 1
                    # Check if value is an arrow function
                    if value_node and value_node.type == "arrow_function":
                        params = value_node.child_by_field_name("parameters")
                        sig = f"const {vname} = " + (params.text.decode("utf-8") if params else "()")
                        is_async = any(c.type == "async" for c in value_node.children)
                        nodes.append(NodeEvent(
                            kind="FUNCTION", name=vname, file_path=fp,
                            line_start=line, line_end=value_node.end_point[0]+1,
                            signature=sig + " =>", parent_id=pid,
                            language=self.language, is_async=is_async,
                        ))
                        fnid = make_node_id(fp, vname, "FUNCTION")
                        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid,
                                             to_id=fnid, line=line))
                        body = value_node.child_by_field_name("body")
                        if body:
                            for c in body.children:
                                self._walk(c, fp, fid, fnid, nodes, edges)
                    else:
                        nodes.append(NodeEvent(
                            kind="VARIABLE", name=vname, file_path=fp,
                            line_start=line, line_end=child.end_point[0]+1,
                            parent_id=pid, language=self.language,
                        ))
                        vid = make_node_id(fp, vname, "VARIABLE")
                        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid,
                                             to_id=vid, line=line))

    def _handle_call(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        func = node.child_by_field_name("function")
        if not func:
            return
        callee = func.text.decode("utf-8")
        line = node.start_point[0] + 1
        confidence = 1.0 if "." not in callee else 0.7
        edges.append(EdgeEvent(
            kind="CALLS", from_id=caller_id,
            to_id=make_node_id(fp, callee, "FUNCTION"),
            line=line, confidence=confidence,
        ))

    # ── JSX support ───────────────────────────────────────────────────

    def _handle_jsx_tag(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        """Extract a CALLS edge from a JSX tag to the component it references.

        <ComponentName /> or <ComponentName> is semantically a call to
        ComponentName(). Only PascalCase identifiers are treated as components;
        lowercase tags (div, span, …) are native HTML and ignored.
        Member expressions (e.g. <ui.Button />) are always component references.
        """
        res = self._jsx_tag_name(node)
        if res is None:
            return
        tag_name, is_member = res
        # Member expressions are always components; plain identifiers must be PascalCase
        if is_member or tag_name[0].isupper():
            line = node.start_point[0] + 1
            edges.append(EdgeEvent(
                kind="CALLS", from_id=caller_id,
                to_id=make_node_id(fp, tag_name, "FUNCTION"),
                line=line, confidence=0.9,
            ))

    def _handle_jsx_element(self, node: Node, fp: str, fid: str, pid: Optional[str],
                            nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        """Walk a jsx_element: process its opening tag, then recurse children."""
        for child in node.children:
            if child.type == "jsx_opening_element":
                self._handle_jsx_tag(child, fp, pid or fid, edges)
            elif child.type == "jsx_self_closing_element":
                self._handle_jsx_tag(child, fp, pid or fid, edges)
            elif child.type == "jsx_expression":
                # JSX expressions like {items.map(x => <Item />)}
                for c in child.children:
                    self._walk(c, fp, fid, pid, nodes, edges)
            else:
                self._walk(child, fp, fid, pid, nodes, edges)

    @staticmethod
    def _jsx_tag_name(node: Node) -> Optional[tuple[str, bool]]:
        """Extract the tag name from a jsx_opening_element or jsx_self_closing_element.

        Returns:
            A tuple of (tag_name, is_member_expression), or None if no name found.
            Handles plain identifiers (<Header />) and member expressions (<ui.Button />).
        """
        for child in node.children:
            if child.type == "identifier":
                return (child.text.decode("utf-8"), False)
            if child.type == "member_expression":
                return (child.text.decode("utf-8"), True)
        return None
