"""TypeScript language parser using tree-sitter."""

from __future__ import annotations
from typing import Optional

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser as TSParser, Node

from codegraph.parsers.base import Parser, NodeEvent, EdgeEvent, make_node_id

TS_LANGUAGE = Language(tsts.language_typescript())
TSX_LANGUAGE = Language(tsts.language_tsx())


class TypeScriptParser(Parser):
    """Tree-sitter based parser for TypeScript source code.

    Uses language_typescript() for .ts files and language_tsx() for .tsx files,
    ensuring proper JSX parsing in React TypeScript projects.
    """

    language = "typescript"
    extensions = [".ts", ".tsx"]

    def __init__(self) -> None:
        self._ts_parser = TSParser(TS_LANGUAGE)
        self._tsx_parser = TSParser(TSX_LANGUAGE)

    def _parser_for(self, file_path: str) -> TSParser:
        """Select the correct parser based on file extension."""
        if file_path.endswith(".tsx"):
            return self._tsx_parser
        return self._ts_parser

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        """Parse TypeScript source into IR events."""
        parser = self._parser_for(file_path)
        tree = parser.parse(content.encode("utf-8"))
        nodes: list[NodeEvent] = []
        edges: list[EdgeEvent] = []

        file_node = NodeEvent(
            kind="FILE", name=file_path, file_path=file_path,
            line_start=1, line_end=content.count("\n") + 1,
            language=self.language, is_exported=True,
        )
        nodes.append(file_node)
        fid = make_node_id(file_path, file_path, "FILE")
        self._walk(tree.root_node, file_path, fid, None, nodes, edges)
        return nodes, edges

    def _walk(self, node: Node, fp: str, fid: str, pid: Optional[str],
              nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        t = node.type
        if t in ("function_declaration", "generator_function_declaration"):
            self._handle_func(node, fp, fid, pid, nodes, edges)
        elif t == "class_declaration":
            self._handle_class(node, fp, fid, pid, nodes, edges)
        elif t in ("interface_declaration", "type_alias_declaration"):
            self._handle_type(node, fp, fid, pid, nodes, edges)
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
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)
        else:
            for c in node.children:
                self._walk(c, fp, fid, pid, nodes, edges)

    def _handle_func(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8")
        params = node.child_by_field_name("parameters")
        ret = node.child_by_field_name("return_type")
        sig = f"function {name}" + (params.text.decode("utf-8") if params else "()")
        if ret:
            sig += f": {ret.text.decode('utf-8')}"

        nodes.append(NodeEvent(
            kind="FUNCTION", name=name, file_path=fp,
            line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
            signature=sig, parent_id=pid, language=self.language,
            is_async=any(c.type == "async" for c in node.children),
        ))
        func_id = make_node_id(fp, name, "FUNCTION")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=func_id,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                self._walk(c, fp, fid, func_id, nodes, edges)

    def _handle_class(self, node: Node, fp: str, fid: str, pid: Optional[str],
                      nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8")
        nodes.append(NodeEvent(
            kind="CLASS", name=name, file_path=fp,
            line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
            signature=f"class {name}", parent_id=pid, language=self.language,
        ))
        cid = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=cid,
                             line=node.start_point[0]+1))
        body = node.child_by_field_name("body")
        if body:
            for c in body.children:
                if c.type == "method_definition":
                    mn = c.child_by_field_name("name")
                    if mn:
                        mname = mn.text.decode("utf-8")
                        nodes.append(NodeEvent(
                            kind="FUNCTION", name=mname, file_path=fp,
                            line_start=c.start_point[0]+1, line_end=c.end_point[0]+1,
                            parent_id=cid, language=self.language,
                        ))
                        mid = make_node_id(fp, mname, "FUNCTION")
                        edges.append(EdgeEvent(kind="DEFINES", from_id=cid, to_id=mid,
                                             line=c.start_point[0]+1))
                else:
                    self._walk(c, fp, fid, cid, nodes, edges)

    def _handle_type(self, node: Node, fp: str, fid: str, pid: Optional[str],
                     nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8")
        nodes.append(NodeEvent(
            kind="CLASS", name=name, file_path=fp,
            line_start=node.start_point[0]+1, line_end=node.end_point[0]+1,
            signature=node.text.decode("utf-8").split("{")[0].strip(),
            parent_id=pid, language=self.language,
        ))
        tid = make_node_id(fp, name, "CLASS")
        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid, to_id=tid,
                             line=node.start_point[0]+1))

    def _handle_import(self, node: Node, fp: str, fid: str, pid: Optional[str],
                       nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        source = node.child_by_field_name("source")
        if source:
            mod = source.text.decode("utf-8").strip("'\"")
            line = node.start_point[0] + 1
            nodes.append(NodeEvent(kind="IMPORT", name=mod, file_path=fp,
                                  line_start=line, line_end=line, language=self.language))
            imp_id = make_node_id(fp, mod, "IMPORT")
            edges.append(EdgeEvent(kind="IMPORTS", from_id=pid or fid, to_id=imp_id, line=line))

    def _handle_var_decl(self, node: Node, fp: str, fid: str, pid: Optional[str],
                         nodes: list[NodeEvent], edges: list[EdgeEvent]) -> None:
        for c in node.children:
            if c.type == "variable_declarator":
                nn = c.child_by_field_name("name")
                val = c.child_by_field_name("value")
                if nn and nn.type == "identifier":
                    vname = nn.text.decode("utf-8")
                    line = c.start_point[0] + 1
                    if val and val.type == "arrow_function":
                        nodes.append(NodeEvent(
                            kind="FUNCTION", name=vname, file_path=fp,
                            line_start=line, line_end=val.end_point[0]+1,
                            parent_id=pid, language=self.language,
                        ))
                        fnid = make_node_id(fp, vname, "FUNCTION")
                        edges.append(EdgeEvent(kind="DEFINES", from_id=pid or fid,
                                             to_id=fnid, line=line))
                        body = val.child_by_field_name("body")
                        if body:
                            for cc in body.children:
                                self._walk(cc, fp, fid, fnid, nodes, edges)
                    else:
                        nodes.append(NodeEvent(
                            kind="VARIABLE", name=vname, file_path=fp,
                            line_start=line, line_end=c.end_point[0]+1,
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
        edges.append(EdgeEvent(
            kind="CALLS", from_id=caller_id,
            to_id=make_node_id(fp, callee, "FUNCTION"),
            line=line, confidence=1.0 if "." not in callee else 0.7,
        ))

    # ── JSX support ───────────────────────────────────────────────────

    def _handle_jsx_tag(self, node: Node, fp: str, caller_id: str, edges: list[EdgeEvent]) -> None:
        """Extract a CALLS edge from a JSX tag to the component it references.

        <ComponentName /> or <ComponentName> is semantically a call to
        ComponentName(). Only PascalCase identifiers are treated as components;
        lowercase tags (div, span, …) are native HTML and ignored.
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
