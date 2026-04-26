"""File content hashing utilities for change detection."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_content(content: str) -> str:
    """Compute SHA-256 hash of string content.

    Args:
        content: The string content to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hash_file(file_path: str | Path) -> str:
    """Compute SHA-256 hash of a file's contents.

    Args:
        file_path: Path to the file to hash.

    Returns:
        Hex-encoded SHA-256 digest.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(file_path)
    return hashlib.sha256(path.read_bytes()).hexdigest()
