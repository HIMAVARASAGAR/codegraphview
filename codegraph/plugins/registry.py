"""Plugin registry for CodeGraph."""
from __future__ import annotations

import logging
from typing import Optional

from codegraph.plugins.base import Plugin

logger = logging.getLogger(__name__)

_PLUGINS: dict[str, Plugin] = {}


def register_plugin(plugin: Plugin) -> None:
    """Register a plugin instance.

    Args:
        plugin: The plugin instance to register.
    """
    _PLUGINS[plugin.name] = plugin
    logger.info("Registered plugin: %s v%s", plugin.name, plugin.version)


def get_plugin(name: str) -> Optional[Plugin]:
    """Get a registered plugin by name.

    Args:
        name: The plugin name.

    Returns:
        The plugin instance, or None.
    """
    return _PLUGINS.get(name)


def list_plugins() -> list[Plugin]:
    """List all registered plugins.

    Returns:
        List of registered plugin instances.
    """
    return list(_PLUGINS.values())


def notify_scan_complete(stats: dict) -> None:
    """Notify all plugins that a scan is complete.

    Args:
        stats: Graph statistics dict.
    """
    for plugin in _PLUGINS.values():
        try:
            plugin.on_scan_complete(stats)
        except Exception as e:
            logger.error("Plugin %s error on scan_complete: %s", plugin.name, e)


def notify_file_patched(file_path: str, node_count: int, edge_count: int) -> None:
    """Notify all plugins that a file was patched.

    Args:
        file_path: Path to the patched file.
        node_count: Number of nodes emitted.
        edge_count: Number of edges emitted.
    """
    for plugin in _PLUGINS.values():
        try:
            plugin.on_file_patched(file_path, node_count, edge_count)
        except Exception as e:
            logger.error("Plugin %s error on file_patched: %s", plugin.name, e)
