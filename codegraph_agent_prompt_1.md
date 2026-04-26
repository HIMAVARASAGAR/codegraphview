# CodeGraph — Agent Build Prompt

You are building **CodeGraph**, a production-grade open source developer tool that converts any codebase into a persistent, queryable knowledge graph stored inside the repository itself. This graph is consumed by AI systems (Claude, ChatGPT, Cursor, etc.) via MCP instead of reading raw code.

---

## What you are building

A Python CLI tool (`codegraph`) that:
1. Scans a codebase and parses it into a graph of nodes (files, classes, functions, variables) and edges (CALLS, IMPORTS, DEFINES, INHERITS, etc.)
2. Stores this graph in `/codegraph/graph.db` (SQLite) and exports `/codegraph/graph.json`
3. Watches the repo for file changes and incrementally updates only the affected parts of the graph (not a full rebuild)
4. Exposes an MCP (Model Context Protocol) server so any AI tool can query the graph
5. Has a CLI with: `init`, `scan`, `watch`, `query`, `impact`, `dead-code`, `serve`, `export` subcommands

---

## Tech stack

- **Language:** Python 3.11+
- **Parsing:** `tree-sitter` + individual tree-sitter language packages
- **File watching:** `watchdog`
- **Graph storage:** SQLite via stdlib `sqlite3`
- **MCP server:** `mcp` Python SDK
- **CLI:** `click`
- **Package manager:** `uv` (not pip, not poetry)
- **Config:** `pyproject.toml`
- **Testing:** `pytest`

---

## Supported languages at launch (Tier 1)

Python, JavaScript, TypeScript, Java, Go, Rust, C, C++, C#, Ruby, PHP, Swift, Kotlin

Each language has one parser file in `codegraph/parsers/`. Adding a new language = adding one file. The core never changes.

---

## Exact repo structure to create

```
codegraph/
├── codegraph/
│   ├── __init__.py
│   ├── cli.py
│   ├── watcher.py
│   ├── hasher.py
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base.py          ← IR dataclasses + abstract Parser
│   │   ├── python.py
│   │   ├── javascript.py
│   │   ├── typescript.py
│   │   ├── java.py
│   │   ├── go.py
│   │   ├── rust.py
│   │   ├── c.py
│   │   ├── cpp.py
│   │   ├── csharp.py
│   │   ├── ruby.py
│   │   ├── php.py
│   │   ├── swift.py
│   │   └── kotlin.py
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── store.py         ← SQLite schema + read/write
│   │   ├── patcher.py       ← incremental update logic
│   │   ├── exporter.py      ← SQLite → graph.json
│   │   └── query.py         ← traversal + analysis
│   ├── context/
│   │   ├── __init__.py
│   │   ├── builder.py       ← AI context snapshot builder
│   │   └── summarizer.py    ← lazy AI summary generation
│   ├── mcp_server.py
│   └── plugins/
│       ├── __init__.py
│       ├── base.py
│       └── registry.py
├── tests/
│   ├── __init__.py
│   ├── test_parsers.py
│   ├── test_graph.py
│   └── test_context.py
├── pyproject.toml
├── .codegraphignore          ← default ignore patterns
└── README.md
```

---

## Core data model — implement exactly this

### IR (Intermediate Representation) — `parsers/base.py`

Every language parser emits these — nothing else. All downstream code is language-agnostic.

```python
from dataclasses import dataclass, field
from typing import Literal, Optional

NodeKind = Literal["FILE", "MODULE", "CLASS", "FUNCTION", "VARIABLE", "IMPORT"]
EdgeKind = Literal["DEFINES", "CALLS", "IMPORTS", "INHERITS", "INSTANTIATES", "READS", "WRITES"]

@dataclass
class NodeEvent:
    kind: NodeKind
    name: str
    file_path: str
    line_start: int
    line_end: int
    signature: Optional[str] = None
    parent_id: Optional[str] = None   # e.g. class this function belongs to
    language: str = ""
    is_async: bool = False
    is_exported: bool = False
    decorators: list[str] = field(default_factory=list)

@dataclass
class EdgeEvent:
    kind: EdgeKind
    from_id: str                       # sha256(file_path + "::" + name + "::" + kind)
    to_id: str
    line: int
    confidence: float = 1.0           # 1.0 = certain, 0.7 = inferred/dynamic

class Parser:
    """Abstract base. Each language implements this."""
    language: str = ""
    extensions: list[str] = []

    def parse(self, file_path: str, content: str) -> tuple[list[NodeEvent], list[EdgeEvent]]:
        raise NotImplementedError
```

### Node ID generation — use this everywhere

```python
import hashlib

def make_node_id(file_path: str, name: str, kind: str) -> str:
    raw = f"{file_path}::{name}::{kind}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

---

## SQLite schema — `graph/store.py`

```sql
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    line_start  INTEGER,
    line_end    INTEGER,
    signature   TEXT,
    language    TEXT,
    file_hash   TEXT,
    ai_summary  TEXT,
    complexity  INTEGER,
    is_async    INTEGER DEFAULT 0,
    is_exported INTEGER DEFAULT 0,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    from_id     TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    line        INTEGER,
    confidence  REAL DEFAULT 1.0,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS file_hashes (
    file_path   TEXT PRIMARY KEY,
    hash        TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_from     ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to       ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_nodes_file     ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_name     ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind     ON nodes(kind);
```

---

## Incremental patching logic — `graph/patcher.py`

This is critical for performance. Implement exactly this flow:

```
on file_changed(file_path):
  1. compute SHA-256 of file content
  2. look up stored hash in file_hashes table
  3. if hashes match → return immediately (no work)
  4. select the correct Parser by file extension
  5. call parser.parse(file_path, content) → (node_events, edge_events)
  6. DELETE FROM nodes WHERE file_path = ?  (cascade deletes edges too)
  7. INSERT all new nodes
  8. INSERT all new edges (skip edges where to_id not in nodes — unresolved refs)
  9. UPDATE file_hashes SET hash = new_hash
  10. regenerate graph.json export (async, non-blocking)

on file_deleted(file_path):
  1. DELETE FROM nodes WHERE file_path = ?
  2. DELETE FROM file_hashes WHERE file_path = ?
  3. regenerate graph.json export
```

The worker pool has 4 threads by default (configurable via `CODEGRAPH_WORKERS` env var). File change events go onto a `queue.Queue`. Workers pull from the queue. This prevents thundering herd on `git checkout`.

---

## AI context builder — `context/builder.py`

Takes a focal point, returns a token-budget-aware JSON snapshot. Default budget: 4000 tokens (~16000 chars).

Output format:

```json
{
  "focal": {
    "id": "abc123",
    "kind": "FUNCTION",
    "name": "processPayment",
    "file": "src/payments/processor.py",
    "signature": "def processPayment(amount: int, card_id: str) -> PaymentResult",
    "summary": "...",
    "lines": [45, 89],
    "complexity": 12
  },
  "called_by": [
    { "id": "...", "name": "checkoutHandler", "file": "...", "summary": "..." }
  ],
  "calls": [
    { "id": "...", "name": "db.save_transaction", "file": "...", "summary": "...", "confidence": 1.0 }
  ],
  "siblings": [],
  "impact_if_changed": ["checkoutHandler", "retryQueue", "adminRefundView"],
  "meta": {
    "graph_version": "1.0",
    "generated_at": "2025-01-01T00:00:00Z",
    "token_estimate": 1240,
    "budget_used_pct": 31
  }
}
```

Budget allocation order (fill until budget exhausted):
1. Focal node full detail (~400 tokens)
2. Direct callers, up to 10 (~800 tokens)
3. Direct callees, up to 10 (~800 tokens)
4. File siblings (other functions in same file), summarized (~400 tokens)
5. Impact list, names only (~200 tokens)
6. 2-hop neighbors, names + file only (remaining budget)

---

## MCP server — `mcp_server.py`

Expose exactly these three tools using the `mcp` Python SDK:

```python
@mcp.tool()
def get_context(focal: str, budget_tokens: int = 4000) -> dict:
    """
    Get AI-ready context snapshot for a function, class, or file.
    focal: function name, class name, or file path
    """

@mcp.tool()
def search_graph(query: str, kind: str = None, limit: int = 20) -> list[dict]:
    """
    Search nodes by name. kind filters to FUNCTION, CLASS, FILE, etc.
    Returns list of nodes with name, file, summary, id.
    """

@mcp.tool()
def get_impact(node_id: str) -> dict:
    """
    Returns all nodes that directly or transitively depend on this node.
    Used for impact analysis before making a change.
    """
```

Default port: 6789. Configurable via `--port` flag or `CODEGRAPH_PORT` env var.

---

## CLI interface — `cli.py`

```bash
codegraph init                          # create /codegraph/ dir, write schema, write .codegraphignore
codegraph scan [--path .] [--workers 4] # full scan, builds graph from scratch
codegraph watch [--path .]              # start daemon, incremental updates
codegraph query <name>                  # search nodes, print context snapshot
codegraph impact <name>                 # print impact list
codegraph dead-code                     # print nodes with no incoming edges (excluding entry points)
codegraph serve [--port 6789]           # start MCP server
codegraph export [--focal <name>]       # print AI context JSON to stdout
codegraph stats                         # print graph stats: node count, edge count, languages, etc.
```

---

## Default `.codegraphignore`

```
node_modules/
__pycache__/
.git/
.venv/
venv/
dist/
build/
*.min.js
*.bundle.js
*.pyc
*.pyo
target/           # Rust
vendor/           # Go
.gradle/
*.class
coverage/
.coverage
htmlcov/
```

---

## graph.json export format

```json
{
  "meta": {
    "version": "1.0",
    "generated_at": "ISO8601",
    "node_count": 1240,
    "edge_count": 4821,
    "languages": ["python", "typescript"],
    "schema": "https://codegraph.dev/schema/v1"
  },
  "nodes": {
    "<id>": {
      "kind": "FUNCTION",
      "name": "processPayment",
      "file": "src/payments/processor.py",
      "lines": [45, 89],
      "signature": "...",
      "language": "python",
      "ai_summary": null,
      "complexity": 12,
      "is_async": false,
      "is_exported": true
    }
  },
  "edges": [
    { "from": "<id>", "to": "<id>", "kind": "CALLS", "line": 67, "confidence": 1.0 }
  ]
}
```

For repos with >500k nodes, shard into `graph.nodes.json` + `graph.edges.json` + `graph.index.json` (per-file node list for targeted reads).

---

## pyproject.toml

```toml
[project]
name = "codegraph"
version = "0.1.0"
description = "Persistent code knowledge graph for AI-assisted development"
requires-python = ">=3.11"
dependencies = [
    "tree-sitter>=0.23.0",
    "tree-sitter-python",
    "tree-sitter-javascript",
    "tree-sitter-typescript",
    "tree-sitter-java",
    "tree-sitter-go",
    "tree-sitter-rust",
    "tree-sitter-c",
    "tree-sitter-cpp",
    "tree-sitter-c-sharp",
    "tree-sitter-ruby",
    "tree-sitter-php",
    "tree-sitter-swift",
    "tree-sitter-kotlin",
    "watchdog>=4.0.0",
    "click>=8.1.0",
    "mcp>=1.0.0",
]

[project.scripts]
codegraph = "codegraph.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

---

## What to implement first (build in this order)

1. `parsers/base.py` — IR dataclasses and abstract Parser
2. `graph/store.py` — SQLite schema creation and basic read/write
3. `parsers/python.py` — full Python parser using tree-sitter (validate the whole pipeline works)
4. `graph/patcher.py` — incremental update logic
5. `graph/exporter.py` — SQLite → graph.json
6. `graph/query.py` — callers, callees, impact traversal
7. `context/builder.py` — AI context snapshot
8. `cli.py` — all subcommands wired up
9. `watcher.py` — file watcher + worker queue
10. `mcp_server.py` — MCP tool definitions
11. Remaining 12 language parsers (same pattern as python.py)
12. `tests/` — at minimum test_parsers.py and test_graph.py
13. `README.md` — quickstart, install, usage

---

## Quality bar

- Every parser must handle malformed/partial code without crashing (tree-sitter is error-tolerant by design, leverage this)
- The watcher must handle rapid successive saves (debounce: ignore events within 50ms of a previous event for the same file)
- `graph.json` must always be valid JSON even if the graph is empty
- `codegraph scan` on a 100k-line Python repo must complete in under 30 seconds
- All public functions must have docstrings
- No hardcoded paths — everything relative to the repo root, which is auto-detected by walking up from cwd looking for `.git`

---

## What NOT to build yet

- No web UI (visualization is a separate project)
- No cloud sync
- No authentication
- No AI summary generation (leave `ai_summary` as null — that's Phase 2)
- No cross-repo analysis

Build the above completely and correctly before touching anything on this list.
