"""Go language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_go as tsgo
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

GO_LANGUAGE = Language(tsgo.language())

class GoParser(Parser):
    """Tree-sitter based parser for Go source code."""
    language = "go"
    extensions = [".go"]

    def __init__(self) -> None:
        self._ts = TSParser(GO_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse Go source into IR events."""
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
        elif t == "method_declaration":
            self._handle_func(node, fp, fid, pid, nodes, edges)
        elif t == "type_declaration":
            self._handle_type(node, fp, fid, pid, nodes, edges)
        elif t == "import_declaration":
            self._handle_import(node, fp, fid, pid, nodes, edges)
        elif t in ("short_var_declaration", "var_declaration"):
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
        params = node.child_by_field_name("parameters")
        sig = f"func {name}" + (params.text.decode("utf-8") if params else "()")
        exported = name[0].isupper() if name else False
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=sig, parent_id=pid, language=self.language,
                              is_exported=exported))
        func_id = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=func_id,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, func_id, nodes, edges)

    def _handle_type(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for c in node.children:
            if c.type == "type_spec":
                nn = c.child_by_field_name("name")
                if nn:
                    name = nn.text.decode("utf-8")
                    nodes.append(NodeEvent(kind="CLASS", name=name, file_path=fp,
                                          line_start=c.start_point[0]+1, line_end=c.end_point[0]+1,
                                          signature=f"type {name}", parent_id=pid,
                                          language=self.language,
                                          is_exported=name[0].isupper() if name else False))
                    tid = make_node_id(fp, name, "CLASS")
                    edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=tid,
                                         line=c.start_point[0]+1))

    def _handle_import(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for c in node.children:
            if c.type == "import_spec":
                path_node = c.child_by_field_name("path")
                if path_node:
                    mod = path_node.text.decode("utf-8").strip('"')
                    line = c.start_point[0] + 1
                    nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                          line_start=line, line_end=line, language=self.language))
                    iid = make_node_id(fp, mod, "IMPORT")
                    edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))
            elif c.type == "import_spec_list":
                for spec in c.children:
                    if spec.type == "import_spec":
                        pn = spec.child_by_field_name("path")
                        if pn:
                            mod = pn.text.decode("utf-8").strip('"')
                            line = spec.start_point[0] + 1
                            nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                                  line_start=line, line_end=line, language=self.language))
                            iid = make_node_id(fp, mod, "IMPORT")
                            edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))

    def _handle_var(self, node: Node, fp: str, fid: str, pid: Optional[str],
                    nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for c in node.children:
            if c.type == "identifier":
                name = c.text.decode("utf-8")
                line = c.start_point[0] + 1
                nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                                      line_start=line, line_end=node.end_point[0]+1,
                                      parent_id=pid, language=self.language))
                vid = make_node_id(fp, name, "VARIABLE")
                edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=vid, line=line))
                break
            elif c.type == "expression_list":
                for ident in c.children:
                    if ident.type == "identifier":
                        name = ident.text.decode("utf-8")
                        line = ident.start_point[0] + 1
                        nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                                              line_start=line, line_end=node.end_point[0]+1,
                                              parent_id=pid, language=self.language))
                        vid = make_node_id(fp, name, "VARIABLE")
                        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=vid, line=line))
                break

    def _handle_call(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        func = node.child_by_field_name("function")
        if not func: return
        callee = func.text.decode("utf-8")
        line = node.start_point[0] + 1
        edges.append(EdgeEvent(kind="CALLS", from_id=caller_id,
                             to_id=make_node_id(fp, callee, "FUNCTION"),
                             line=line, confidence=1.0 if "." not in callee else 0.7))
