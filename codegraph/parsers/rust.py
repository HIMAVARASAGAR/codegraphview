"""Rust language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_rust as tsrust
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

RUST_LANGUAGE = Language(tsrust.language())

class RustParser(Parser):
    """Tree-sitter based parser for Rust source code."""
    language = "rust"
    extensions = [".rs"]

    def __init__(self) -> None:
        self._ts = TSParser(RUST_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse Rust source into IR events."""
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
        if t == "function_item":
            self._handle_func(node, fp, fid, pid, nodes, edges)
        elif t in ("struct_item", "enum_item", "trait_item"):
            self._handle_struct(node, fp, fid, pid, nodes, edges)
        elif t == "impl_item":
            self._handle_impl(node, fp, fid, pid, nodes, edges)
        elif t == "use_declaration":
            self._handle_use(node, fp, fid, pid, nodes, edges)
        elif t in ("let_declaration", "const_item", "static_item"):
            self._handle_var(node, fp, fid, pid, nodes, edges)
        elif t == "call_expression":
            self._handle_call(node, fp, pid or fid, edges)
        elif t == "mod_item":
            self._handle_mod(node, fp, fid, pid, nodes, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_func(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        params = node.child_by_field_name("parameters")
        sig = f"fn {name}" + (params.text.decode("utf-8") if params else "()")
        is_pub = any(c.type == "visibility_modifier" for c in node.children)
        is_async = any(c.type == "async" for c in node.children)
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=sig, parent_id=pid, language=self.language,
                              is_exported=is_pub, is_async=is_async))
        func_id = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=func_id,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, func_id, nodes, edges)

    def _handle_struct(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        kind_str = node.type.replace("_item", "")
        nodes.append(NodeEvent(kind="CLASS", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"{kind_str} {name}", parent_id=pid,
                              language=self.language,
                              is_exported=any(c.type == "visibility_modifier" for c in node.children)))
        sid = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=sid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, sid, nodes, edges)

    def _handle_impl(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        # Find the type being implemented
        type_node = node.child_by_field_name("type")
        if not type_node: return
        type_name = type_node.text.decode("utf-8")
        impl_id = make_node_id(fp, type_name, "CLASS")
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, impl_id, nodes, edges)

    def _handle_use(self, node: Node, fp: str, fid: str, pid: Optional[str],
                    nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        # Extract the use path
        for c in node.children:
            if c.type in ("use_as_clause", "scoped_use_list", "use_wildcard", "scoped_identifier", "identifier"):
                mod = c.text.decode("utf-8")
                line = node.start_point[0] + 1
                nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                      line_start=line, line_end=line, language=self.language))
                iid = make_node_id(fp, mod, "IMPORT")
                edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))
                break

    def _handle_var(self, node: Node, fp: str, fid: str, pid: Optional[str],
                    nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name") or node.child_by_field_name("pattern")
        if not nn: return
        name = nn.text.decode("utf-8")
        line = node.start_point[0] + 1
        nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                              line_start=line, line_end=node.end_point[0]+1,
                              parent_id=pid, language=self.language))
        vid = make_node_id(fp, name, "VARIABLE")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=vid, line=line))

    def _handle_mod(self, node: Node, fp: str, fid: str, pid: Optional[str],
                    nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        nodes.append(NodeEvent(kind="MODULE", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              parent_id=pid, language=self.language))
        mid = make_node_id(fp, name, "MODULE")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=mid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, mid, nodes, edges)

    def _handle_call(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        func = node.child_by_field_name("function")
        if not func: return
        callee = func.text.decode("utf-8")
        line = node.start_point[0] + 1
        edges.append(EdgeEvent(kind="CALLS", from_id=caller_id,
                             to_id=make_node_id(fp, callee, "FUNCTION"),
                             line=line, confidence=0.8))
