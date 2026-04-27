"""CodeGraph CLI — all subcommands.

Provides init, scan, watch, query, impact, dead-code, serve, export, and stats.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import click

from codegraph import __version__

# ── Helpers ───────────────────────────────────────────────────────────

def _find_repo_root(start: Path | None = None) -> Path:
    """Walk up from start looking for .git to find the repo root.

    Falls back to cwd if no .git found.
    """
    p = start or Path.cwd()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def _codegraph_dir(repo_root: Path) -> Path:
    """Get the codegraph data directory."""
    return repo_root / ".codegraph"


def _get_store(repo_root: Path):
    """Get a GraphStore instance for the repo."""
    from codegraph.graph.store import GraphStore
    db_path = _codegraph_dir(repo_root) / "graph.db"
    store = GraphStore(db_path)
    store.connect()
    return store


def _load_ignore_patterns(repo_root: Path) -> list[str]:
    """Load ignore patterns from .codegraphignore."""
    ignore_file = repo_root / ".codegraphignore"
    if not ignore_file.exists():
        return []
    patterns = []
    for line in ignore_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _should_ignore(file_path: str, patterns: list[str]) -> bool:
    """Check if a file should be ignored based on patterns."""
    import fnmatch
    for pattern in patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
        if fnmatch.fnmatch(file_path, f"*/{pattern}"):
            return True
        # Check directory patterns
        if pattern.endswith("/") and f"/{pattern}" in f"/{file_path}/":
            return True
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            parts = Path(file_path).parts
            if dir_name in parts:
                return True
    return False


def _collect_files(repo_root: Path, patterns: list[str]) -> list[Path]:
    """Collect all parseable files in the repo, respecting ignore patterns."""
    from codegraph.graph.patcher import has_parser_for_file
    files = []
    for path in repo_root.rglob("*"):
        if path.is_dir():
            continue
        try:
            rel = str(path.relative_to(repo_root))
        except ValueError:
            continue
        if _should_ignore(rel, patterns):
            continue
        # Check if any parser supports this file (no module imports needed)
        if has_parser_for_file(rel):
            files.append(path)
    return files


# ── CLI group ─────────────────────────────────────────────────────────

@click.group()
@click.version_option(__version__, prog_name="codegraph")
def main():
    """CodeGraph — Persistent code knowledge graph for AI-assisted development."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


# ── init ──────────────────────────────────────────────────────────────

@main.command()
@click.option("--path", default=".", help="Repository root path.")
def init(path: str):
    """Initialize CodeGraph in a repository."""
    repo_root = Path(path).resolve()
    cg_dir = _codegraph_dir(repo_root)
    cg_dir.mkdir(parents=True, exist_ok=True)

    # Create .codegraphignore if not exists
    ignore_file = repo_root / ".codegraphignore"
    if not ignore_file.exists():
        default_ignore = (Path(__file__).parent.parent / ".codegraphignore")
        if default_ignore.exists():
            ignore_file.write_text(default_ignore.read_text())
        else:
            ignore_file.write_text(
                "node_modules/\n__pycache__/\n.git/\n.venv/\nvenv/\n"
                "dist/\nbuild/\n*.min.js\n*.bundle.js\n*.pyc\n*.pyo\n"
                "target/\nvendor/\n.gradle/\n*.class\ncoverage/\n"
                ".coverage\nhtmlcov/\n.codegraph/\n"
            )

    # Add .codegraph/ to .gitignore
    gitignore_file = repo_root / ".gitignore"
    if gitignore_file.exists():
        content = gitignore_file.read_text()
        if ".codegraph" not in content and ".codegraph/" not in content:
            with gitignore_file.open("a") as f:
                f.write("\n# CodeGraph\n.codegraph/\n")
    else:
        gitignore_file.write_text("# CodeGraph\n.codegraph/\n")

    # Initialize database
    store = _get_store(repo_root)
    store.set_meta("version", "1.0")
    store.set_meta("repo_root", str(repo_root))
    store.close()

    click.echo(f"✓ Initialized CodeGraph in {cg_dir}")
    click.echo(f"  Database: {cg_dir / 'graph.db'}")
    click.echo(f"  Ignore:   {ignore_file}")


# ── scan ──────────────────────────────────────────────────────────────

@main.command()
@click.option("--path", default=".", help="Repository root path.")
@click.option("--workers", default=1, help="Number of worker threads (1=sequential, >1=parallel).")
def scan(path: str, workers: int):
    """Full scan — build graph from scratch."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)

    from codegraph.graph.patcher import Patcher, PatchWorkerPool

    patcher = Patcher(store, repo_root)
    patterns = _load_ignore_patterns(repo_root)
    files = _collect_files(repo_root, patterns)

    click.echo(f"Scanning {len(files)} files with {workers} workers...")
    start = time.time()

    if workers <= 1:
        # Single-threaded scan — avoids tree-sitter C extension threading issues
        for f in files:
            try:
                patcher.patch_file(str(f))
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error("Error processing %s: %s", f, e)
    else:
        pool = PatchWorkerPool(patcher, num_workers=workers)
        pool.start()
        for f in files:
            pool.submit("modified", str(f))
        pool.queue.join()
        pool.stop()

    elapsed = time.time() - start
    stats = store.stats()
    store.close()

    click.echo(f"✓ Scan complete in {elapsed:.1f}s")
    click.echo(f"  Nodes:     {stats['node_count']}")
    click.echo(f"  Edges:     {stats['edge_count']}")
    click.echo(f"  Languages: {', '.join(stats['languages']) or 'none'}")
    click.echo(f"  Files:     {stats['file_count']}")


# ── watch ─────────────────────────────────────────────────────────────

@main.command()
@click.option("--path", default=".", help="Repository root path.")
def watch(path: str):
    """Watch for file changes and incrementally update the graph."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)

    from codegraph.watcher import start_watcher

    click.echo(f"Watching {repo_root} for changes... (Ctrl+C to stop)")
    try:
        start_watcher(store, repo_root)
    except KeyboardInterrupt:
        click.echo("\n✓ Watcher stopped.")
    finally:
        store.close()


# ── query ─────────────────────────────────────────────────────────────

@main.command()
@click.argument("name")
@click.option("--path", default=".", help="Repository root path.")
def query(name: str, path: str):
    """Search nodes and print context snapshot."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)

    from codegraph.context.builder import ContextBuilder

    builder = ContextBuilder(store)
    ctx = builder.build(name)
    click.echo(json.dumps(ctx, indent=2))
    store.close()


# ── impact ────────────────────────────────────────────────────────────

@main.command()
@click.argument("name")
@click.option("--path", default=".", help="Repository root path.")
def impact(name: str, path: str):
    """Print impact analysis for a node."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)

    from codegraph.graph.query import QueryEngine

    engine = QueryEngine(store)
    node = engine.find_node_by_name(name)
    if not node:
        click.echo(f"Node not found: {name}", err=True)
        store.close()
        sys.exit(1)

    impacted = engine.impact_analysis(node["id"])
    click.echo(f"Impact analysis for '{name}' ({node['kind']}):")
    click.echo(f"  {len(impacted)} nodes affected:\n")
    for n in impacted:
        depth = n.get("impact_depth", "?")
        click.echo(f"  [{depth}] {n['kind']:10s} {n['name']:30s} {n['file_path']}")
    store.close()


# ── dead-code ─────────────────────────────────────────────────────────

@main.command("dead-code")
@click.option("--path", default=".", help="Repository root path.")
def dead_code(path: str):
    """Print nodes with no incoming edges (potential dead code)."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)

    from codegraph.graph.query import QueryEngine

    engine = QueryEngine(store)
    dead = engine.dead_code()

    click.echo(f"Found {len(dead)} potentially unused nodes:\n")
    for n in dead:
        click.echo(f"  {n['kind']:10s} {n['name']:30s} {n['file_path']}:{n.get('line_start', '?')}")
    store.close()


# ── serve ─────────────────────────────────────────────────────────────

@main.command()
@click.option("--port", default=6789, help="MCP server port.")
@click.option("--path", default=".", help="Repository root path.")
def serve(port: int, path: str):
    """Start the MCP server."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)

    from codegraph.mcp_server import create_server

    click.echo(f"Starting MCP server on port {port}...")
    server = create_server(store)
    try:
        server.run(transport="stdio")
    except KeyboardInterrupt:
        click.echo("\n✓ MCP server stopped.")
    finally:
        store.close()


# ── export ────────────────────────────────────────────────────────────

@main.command()
@click.option("--focal", default=None, help="Focal point for context export.")
@click.option("--path", default=".", help="Repository root path.")
def export(focal: Optional[str], path: str):
    """Export graph or context JSON to stdout."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)

    if focal:
        from codegraph.context.builder import ContextBuilder
        builder = ContextBuilder(store)
        ctx = builder.build(focal)
        click.echo(json.dumps(ctx, indent=2))
    else:
        from codegraph.graph.exporter import Exporter
        export_path = _codegraph_dir(repo_root) / "graph.json"
        exporter = Exporter(store, export_path)
        result = exporter.export()
        click.echo(f"✓ Exported to {result}")
    store.close()


# ── stats ─────────────────────────────────────────────────────────────

@main.command()
@click.option("--path", default=".", help="Repository root path.")
def stats(path: str):
    """Print graph statistics."""
    repo_root = Path(path).resolve()
    store = _get_store(repo_root)
    s = store.stats()

    click.echo("CodeGraph Statistics")
    click.echo("─" * 40)
    click.echo(f"  Nodes:     {s['node_count']}")
    click.echo(f"  Edges:     {s['edge_count']}")
    click.echo(f"  Files:     {s['file_count']}")
    click.echo(f"  Languages: {', '.join(s['languages']) or 'none'}")
    store.close()


if __name__ == "__main__":
    main()
