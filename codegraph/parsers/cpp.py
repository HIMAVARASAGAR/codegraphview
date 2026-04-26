"""C++ language parser using tree-sitter."""
from __future__ import annotations
from typing import Optional
import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser as TSParser, Node
from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

CPP_LANGUAGE = Language(tscpp.language())

class CppParser(Parser):
    """Tree-sitter based parser for C++ source code."""
    language = "cpp"
    extensions = [".cpp", ".cxx", ".cc", ".hpp", ".hxx", ".hh"]

    def __init__(self) -> None:
        self._ts = TSParser(CPP_LANGUAGE)

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse C++ source into IR events."""
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
        elif t == "class_specifier":
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t in ("struct_specifier", "enum_specifier"):
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t == "namespace_definition":
            self._handle_namespace(node, fp, fid, pid, nodes, edges)
        elif t == "preproc_include":
            self._handle_include(node, fp, fid, pid, nodes, edges)
        elif t == "declaration":
            self._handle_decl(node, fp, fid, pid, nodes, edges)
        elif t == "call_expression":
            self._handle_call(node, fp, pid or fid, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_func(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        decl = node.child_by_field_name("declarator")
        if not decl: return
        name = self._extract_name(decl)
        if not name: return
        nodes.append(NodeEvent(kind="FUNCTION", name=name, file_path=fp,
                              line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
                              signature=f"{name}()", parent_id=pid, language=self.language))
        func_id = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=func_id,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, func_id, nodes, edges)

    def _extract_name(self, node: Node) -> Optional[str]:
        if node.type == "identifier":
            return node.text.decode("utf-8")
        if node.type == "qualified_identifier":
            return node.text.decode("utf-8")
        if node.type in ("function_declarator", "pointer_declarator", "reference_declarator"):
            decl = node.child_by_field_name("declarator")
            if decl:
                return self._extract_name(decl)
        for c in node.children:
            r = self._extract_name(c)
            if r: return r
        return None

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

    def _handle_include(self, node: Node, fp: str, fid: str, pid: Optional[str],
                        nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        path_node = node.child_by_field_name("path")
        if path_node:
            mod = path_node.text.decode("utf-8").strip('<>"')
            line = node.start_point[0] + 1
            nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                  line_start=line, line_end=line, language=self.language))
            iid = make_node_id(fp, mod, "IMPORT")
            edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=iid, line=line))

    def _handle_decl(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        decl = node.child_by_field_name("declarator")
        if decl:
            name = self._extract_name(decl)
            if name and "(" not in name:
                line = node.start_point[0] + 1
                nodes.append(NodeEvent(kind="VARIABLE", name=name, file_path=fp,
                                      line_start=line, line_end=node.end_point[0]+1,
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
