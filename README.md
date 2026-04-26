# CodeGraph

**Persistent code knowledge graph for AI-assisted development.**

CodeGraph scans any codebase, builds a queryable knowledge graph of all code entities and their relationships, and exposes it via MCP so AI tools (Claude, ChatGPT, Cursor, etc.) can understand your code structure without reading raw files.

## Features

- **Multi-language support** — Python, JavaScript, TypeScript, Java, Go, Rust, C, C++, C#, Ruby, PHP, Swift, Kotlin
- **Incremental updates** — Only re-parses changed files, making `git checkout` fast
- **MCP server** — AI tools query the graph via Model Context Protocol
- **Token-budget-aware context** — Produces AI-ready snapshots that fit within token limits
- **Impact analysis** — Know what breaks before you change it
- **Dead code detection** — Find unused functions and classes
- **SQLite storage** — Zero external dependencies, ships with your repo

## Quickstart

### Install

```bash
# Using uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

### Initialize

```bash
cd your-project
codegraph init
```

This creates a `codegraph/` directory with the SQLite database and a `.codegraphignore` file.

### Scan

```bash
codegraph scan
```

Full scan of the codebase. Builds the graph from scratch.

### Watch

```bash
codegraph watch
```

Starts a daemon that watches for file changes and incrementally updates the graph.

### Query

```bash
codegraph query processPayment
```

Search for a function/class and print its AI-ready context snapshot.

### Impact Analysis

```bash
codegraph impact processPayment
```

Show all nodes that would be affected if `processPayment` changes.

### Dead Code Detection

```bash
codegraph dead-code
```

List functions and classes with no incoming references.

### Start MCP Server

```bash
codegraph serve --port 6789
```

Starts the MCP server so AI tools can query the graph.

### Export

```bash
# Export full graph to codegraph/graph.json
codegraph export

# Export context snapshot for a specific function
codegraph export --focal processPayment
```

### Stats

```bash
codegraph stats
```

## MCP Tools

When running `codegraph serve`, the following tools are available:

| Tool | Description |
|------|-------------|
| `get_context(focal, budget_tokens)` | Get AI-ready context snapshot for a function, class, or file |
| `search_graph(query, kind, limit)` | Search nodes by name with optional kind filter |
| `get_impact(node_id)` | Transitive impact analysis for a node |

## Architecture

```
Source Code → Tree-sitter Parser → IR (NodeEvent/EdgeEvent) → SQLite Graph → MCP Server → AI
```

### Key Design Decisions

1. **Language-agnostic IR** — All parsers emit the same `NodeEvent`/`EdgeEvent` types. The core never knows about specific languages.
2. **Incremental patching** — File changes trigger hash-based diffing. Only changed files are re-parsed, and only their nodes/edges are replaced.
3. **Token-budget-aware context** — The context builder fills information in priority order until the budget is exhausted.
4. **SQLite WAL mode** — Concurrent reads and writes for the watcher's worker pool.

## Supported Node Kinds

| Kind | Description |
|------|-------------|
| `FILE` | Source file |
| `MODULE` | Module/namespace |
| `CLASS` | Class, struct, interface, enum |
| `FUNCTION` | Function, method, constructor |
| `VARIABLE` | Variable, constant, field |
| `IMPORT` | Import/include statement |

## Supported Edge Kinds

| Kind | Description |
|------|-------------|
| `DEFINES` | Parent defines child |
| `CALLS` | Function calls function |
| `IMPORTS` | File/module imports |
| `INHERITS` | Class inherits from class |
| `INSTANTIATES` | Creates instance of class |
| `READS` | Reads variable |
| `WRITES` | Writes variable |

## Configuration

- `CODEGRAPH_WORKERS` — Number of worker threads (default: 4)
- `CODEGRAPH_PORT` — MCP server port (default: 6789)
- `.codegraphignore` — Gitignore-style patterns for excluding files

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Run specific test file
pytest tests/test_parsers.py -v
```

## License

MIT
