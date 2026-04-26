"""Kotlin language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_kotlin as tskotlin
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

KOTLIN_LANGUAGE = Language(tskotlin.language())

class KotlinParser(Parser):
    """Tree-sitter based parser for Kotlin source code."""
    language = "kotlin"
    extensions = [".kt", ".kts"]

    def __init__(self) -> None:
        self._ts = TSParser(KOTLIN_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse Kotlin source into IR events."""
        tree = self._ts.parse(content.encode("utf-8"))
        nodes: list[NodeEvent] = []
        edges: list[EdgeEvent] = []
        nodes.append(NodeEvent(kind="FILE", name=file_path, file_path=file_path,
                              line_start=1, line_end=content.count("\n")+1,
                              language=self.language, is_exported=True))
        fid = make_node_id(file_path, file_path, "FILE")
        self._walk(tree.root_node, file_path, fid, None, nodes, edges)
        return nodes, edges

    def _walk(self, node: Node, fp: str, fid: str, pid: Optional[str],
              nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        t = node.type
        if t == "function_declaration":
            self._handle_func(node, fp, fid, pid, nodes, edges)
        elif t in ("class_declaration", "object_declaration", "interface_declaration"):
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t == "import_header":
            self._handle_import(node, fp, fid, pid, nodes, edges)
        elif t in ("property_declaration",):
            self._handle_var(node, fp, fid, pid, nodes, edges)
        elif t == "call_expression":
            self._handle_call(node, fp, pid or fid, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_func(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        # Find simple_identifier for name
        name = None
        for c in node.children:
            if c.type == "simple_identifier":
                name = c.text.decode("utf-8")
                break
        if not name: return
        is_suspend = any(c.text and b"suspend" in c.text for c in node.children if c.type == "modifiers")
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"fun {name}()", parent_id=pid, language=self.language,
                              is_async=is_suspend))
        func_id = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=func_id,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, func_id, nodes, edges)
        else:
            for c in node.children:
                if c.type == "function_body":
                    for cc in c.children:
                        self._walk(cc, fp, fid, func_id, nodes, edges)

    def _handle_class(self, node: Node, fp: str, fid: str, pid: Optional[str],
                      nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        name = None
        for c in node.children:
            if c.type == "type_identifier":
                name = c.text.decode("utf-8")
                break
        if not name: return
        nodes.append(NodeEvent(kind="CLASS", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"class {name}", parent_id=pid, language=self.language))
        cid = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=cid,
                             line=node.start_point[0]+1))
        for c in node.children:
            if c.type == "class_body":
                for cc in c.children:
                    self._walk(cc, fp, fid, cid, nodes, edges)

    def _handle_import(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        # Find identifier
        for c in node.children:
            if c.type == "identifier":
                mod = c.text.decode("utf-8")
                line = node.start_point[0] + 1
                nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                      line_start=line, line_end=line, language=self.language))
                iid = make_node_id(fp, mod, "IMPORT")
                edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))
                break

    def _handle_var(self, node: Node, fp: str, fid: str, pid: Optional[str],
                    nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for c in node.children:
            if c.type == "variable_declaration":
                for cc in c.children:
                    if cc.type == "simple_identifier":
                        name = cc.text.decode("utf-8")
                        line = node.start_point[0] + 1
                        nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                                              line_start=line, line_end=node.end_point[0]+1,
                                              parent_id=pid, language=self.language))
                        vid = make_node_id(fp, name, "VARIABLE")
                        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=vid, line=line))
                        break

    def _handle_call(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        # First child is usually the callee expression
        if not node.children: return
        callee_node = node.children[0]
        callee = callee_node.text.decode("utf-8")
        line = node.start_point[0] + 1
        edges.append(EdgeEvent(kind="CALLS", from_id=caller_id,
                             to_id=make_node_id(fp, callee, "FUNCTION"),
                             line=line, confidence=0.7))
