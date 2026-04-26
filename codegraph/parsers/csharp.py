"""C# language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

CS_LANGUAGE = Language(tscs.language())

class CSharpParser(Parser):
    """Tree-sitter based parser for C# source code."""
    language = "csharp"
    extensions = [".cs"]

    def __init__(self) -> None:
        self._ts = TSParser(CS_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse C# source into IR events."""
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
        if t in ("class_declaration", "interface_declaration", "struct_declaration",
                 "enum_declaration", "record_declaration"):
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t in ("method_declaration", "constructor_declaration"):
            self._handle_method(node, fp, fid, pid, nodes, edges)
        elif t == "namespace_declaration":
            self._handle_namespace(node, fp, fid, pid, nodes, edges)
        elif t == "using_directive":
            self._handle_using(node, fp, fid, pid, nodes, edges)
        elif t in ("field_declaration", "variable_declaration"):
            self._handle_field(node, fp, fid, pid, nodes, edges)
        elif t == "invocation_expression":
            self._handle_call(node, fp, pid or fid, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_class(self, node: Node, fp: str, fid: str, pid: Optional[str],
                      nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        nodes.append(NodeEvent(kind="CLASS", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"class {name}", parent_id=pid, language=self.language,
                              is_exported=True))
        cid = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=cid,
                             line=node.start_point[0]+1))
        # Check base types
        bases = node.child_by_field_name("bases")
        if bases:
            for c in bases.children:
                if c.type == "identifier":
                    base = c.text.decode("utf-8")
                    edges.append(EdgeEvent(kind="INHERITS", from_id=cid,
                                         to_id=make_node_id(fp, base, "CLASS"),
                                         line=node.start_point[0]+1, confidence=0.8))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, cid, nodes, edges)

    def _handle_method(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        params = node.child_by_field_name("parameters")
        sig = f"{name}" + (params.text.decode("utf-8") if params else "()")
        is_async = any(c.text and c.text.decode("utf-8") == "async" for c in node.children if c.type == "modifier")
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=sig, parent_id=pid, language=self.language, is_async=is_async))
        mid = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=mid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, mid, nodes, edges)

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

    def _handle_using(self, node: Node, fp: str, fid: str, pid: Optional[str],
                      nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        # Extract namespace from using directive
        for c in node.children:
            if c.type in ("identifier", "qualified_name"):
                mod = c.text.decode("utf-8")
                line = node.start_point[0] + 1
                nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                      line_start=line, line_end=line, language=self.language))
                iid = make_node_id(fp, mod, "IMPORT")
                edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))
                break

    def _handle_field(self, node: Node, fp: str, fid: str, pid: Optional[str],
                      nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for c in node.children:
            if c.type == "variable_declarator":
                nn = c.child_by_field_name("name") or (c.children[0] if c.children else None)
                if nn and nn.type == "identifier":
                    name = nn.text.decode("utf-8")
                    line = c.start_point[0] + 1
                    nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                                          line_start=line, line_end=c.end_point[0]+1,
                                          parent_id=pid, language=self.language))
                    vid = make_node_id(fp, name, "VARIABLE")
                    edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=vid, line=line))

    def _handle_call(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        func = node.child_by_field_name("function")
        if not func: return
        callee = func.text.decode("utf-8")
        line = node.start_point[0] + 1
        edges.append(EdgeEvent(kind="CALLS", from_id=caller_id,
                             to_id=make_node_id(fp, callee, "FUNCTION"),
                             line=line, confidence=0.8))
