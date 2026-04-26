"""PHP language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_php as tsphp
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

PHP_LANGUAGE = Language(tsphp.language_php())

class PhpParser(Parser):
    """Tree-sitter based parser for PHP source code."""
    language = "php"
    extensions = [".php"]

    def __init__(self) -> None:
        self._ts = TSParser(PHP_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse PHP source into IR events."""
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
        if t == "function_definition":
            self._handle_func(node, fp, fid, pid, nodes, edges)
        elif t == "method_declaration":
            self._handle_func(node, fp, fid, pid, nodes, edges)
        elif t == "class_declaration":
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t in ("interface_declaration", "trait_declaration"):
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t == "namespace_definition":
            self._handle_namespace(node, fp, fid, pid, nodes, edges)
        elif t == "namespace_use_declaration":
            self._handle_use(node, fp, fid, pid, nodes, edges)
        elif t == "function_call_expression":
            self._handle_call(node, fp, pid or fid, edges)
        elif t == "member_call_expression":
            self._handle_call(node, fp, pid or fid, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_func(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        params = node.child_by_field_name("parameters")
        sig = f"function {name}" + (params.text.decode("utf-8") if params else "()")
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=sig, parent_id=pid, language=self.language))
        func_id = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=func_id,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, func_id, nodes, edges)

    def _handle_class(self, node: Node, fp: str, fid: str, pid: Optional[str],
                      nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        nodes.append(NodeEvent(kind="CLASS", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"class {name}", parent_id=pid, language=self.language))
        cid = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=cid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, cid, nodes, edges)

    def _handle_namespace(self, node: Node, fp: str, fid: str, pid: Optional[str],
                          nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        nodes.append(NodeEvent(kind="MODULE", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              parent_id=pid, language=self.language))
        nsid = make_node_id(fp, name, "MODULE")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=nsid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, nsid, nodes, edges)

    def _handle_use(self, node: Node, fp: str, fid: str, pid: Optional[str],
                    nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for c in node.children:
            if c.type in ("namespace_use_clause", "qualified_name"):
                mod = c.text.decode("utf-8")
                line = node.start_point[0] + 1
                nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                      line_start=line, line_end=line, language=self.language))
                iid = make_node_id(fp, mod, "IMPORT")
                edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))

    def _handle_call(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        func = node.child_by_field_name("function") or node.child_by_field_name("name")
        if not func: return
        callee = func.text.decode("utf-8")
        line = node.start_point[0] + 1
        edges.append(EdgeEvent(kind="CALLS", from_id=caller_id,
                             to_id=make_node_id(fp, callee, "FUNCTION"),
                             line=line, confidence=0.7))
