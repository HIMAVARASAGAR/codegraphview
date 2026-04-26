"""Swift language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_swift as tsswift
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

SWIFT_LANGUAGE = Language(tsswift.language())

class SwiftParser(Parser):
    """Tree-sitter based parser for Swift source code."""
    language = "swift"
    extensions = [".swift"]

    def __init__(self) -> None:
        self._ts = TSParser(SWIFT_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse Swift source into IR events."""
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
        elif t in ("class_declaration", "struct_declaration", "protocol_declaration",
                   "enum_declaration"):
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t == "import_declaration":
            self._handle_import(node, fp, fid, pid, nodes, edges)
        elif t in ("property_declaration", "variable_declaration"):
            self._handle_var(node, fp, fid, pid, nodes, edges)
        elif t == "call_expression":
            self._handle_call(node, fp, pid or fid, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_func(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn: return
        name = nn.text.decode("utf-8")
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"func {name}()", parent_id=pid, language=self.language,
                              is_async=any(c.type == "async" for c in node.children)))
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
        kind_str = node.type.replace("_declaration", "")
        nodes.append(NodeEvent(kind="CLASS", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"{kind_str} {name}", parent_id=pid, language=self.language))
        cid = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=cid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, cid, nodes, edges)

    def _handle_import(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        text = node.text.decode("utf-8").replace("import ", "").strip()
        line = node.start_point[0] + 1
        nodes.append(NodeEvent(kind="IMPORT", name=text, file_path=fp,
                              line_start=line, line_end=line, language=self.language))
        iid = make_node_id(fp, text, "IMPORT")
        edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))

    def _handle_var(self, node: Node, fp: str, fid: str, pid: Optional[str],
                    nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        nn = node.child_by_field_name("name")
        if not nn:
            # Try finding pattern binding
            for c in node.children:
                if c.type == "pattern" and c.children:
                    for cc in c.children:
                        if cc.type == "identifier":
                            nn = cc
                            break
        if not nn: return
        name = nn.text.decode("utf-8")
        line = node.start_point[0] + 1
        nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                              line_start=line, line_end=node.end_point[0]+1,
                              parent_id=pid, language=self.language))
        vid = make_node_id(fp, name, "VARIABLE")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=vid, line=line))

    def _handle_call(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        func = node.child_by_field_name("function") or (node.children[0] if node.children else None)
        if not func: return
        callee = func.text.decode("utf-8")
        line = node.start_point[0] + 1
        edges.append(EdgeEvent(kind="CALLS", from_id=caller_id,
                             to_id=make_node_id(fp, callee, "FUNCTION"),
                             line=line, confidence=0.7))
