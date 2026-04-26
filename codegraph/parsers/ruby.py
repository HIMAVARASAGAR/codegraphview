"""Ruby language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_ruby as tsruby
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

RUBY_LANGUAGE = Language(tsruby.language())

class RubyParser(Parser):
    """Tree-sitter based parser for Ruby source code."""
    language = "ruby"
    extensions = [".rb"]

    def __init__(self) -> None:
        self._ts = TSParser(RUBY_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse Ruby source into IR events."""
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
        if t == "method":
            self._handle_method(node, fp, fid, pid, nodes, edges)
        elif t == "singleton_method":
            self._handle_method(node, fp, fid, pid, nodes, edges)
        elif t == "class":
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t == "module":
            self._handle_module(node, fp, fid, pid, nodes, edges)
        elif t == "call":
            self._handle_call(node, fp, pid or fid, edges)
        elif t == "assignment":
            self._handle_assign(node, fp, fid, pid, nodes, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_method(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        params = node.child_by_field_name("parameters")
        sig = f"def {name}" + (params.text.decode("utf-8") if params else "")
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=sig, parent_id=pid, language=self.language))
        mid = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=mid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, mid, nodes, edges)

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
        sc = node.child_by_field_name("superclass")
        if sc:
            base = sc.text.decode("utf-8")
            edges.append(EdgeEvent(kind="INHERITS", from_id=cid,
                                 to_id=make_node_id(fp, base, "CLASS"),
                                 line=node.start_point[0]+1, confidence=0.8))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, cid, nodes, edges)

    def _handle_module(self, node: Node, fp: str, fid: str, pid: Optional[str],
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
        mn = node.child_by_field_name("method")
        if not mn: return
        callee = mn.text.decode("utf-8")
        line = node.start_point[0] + 1
        edges.append(EdgeEvent(kind="CALLS", from_id=caller_id,
                             to_id=make_node_id(fp, callee, "FUNCTION"),
                             line=line, confidence=0.7))

    def _handle_assign(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        left = node.child_by_field_name("left")
        if left and left.type == "identifier":
            name = left.text.decode("utf-8")
            line = node.start_point[0] + 1
            nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                                  line_start=line, line_end=node.end_point[0]+1,
                                  parent_id=pid, language=self.language))
            vid = make_node_id(fp, name, "VARIABLE")
            edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=vid, line=line))
