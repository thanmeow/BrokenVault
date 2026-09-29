"""Validation of the relative paths stored in manifests."""

from pathlib import Path


class PathError(ValueError):
    """A manifest path is unsafe or malformed."""


def check_rel_path(path: object) -> str:
    """Return `path` if it is a safe relative path with '/' separators.

    Rejects empty paths, absolute paths (POSIX or Windows drive), backslashes,
    NUL bytes, and any empty, '.' or '..' component.
    """
    if not isinstance(path, str) or path == "":
        raise PathError(f"path must be a non-empty string: {path!r}")
    if "\\" in path:
        raise PathError(f"path must use '/' separators: {path!r}")
    if "\x00" in path:
        raise PathError(f"path contains a NUL byte: {path!r}")
    if path.startswith("/"):
        raise PathError(f"absolute path not allowed: {path!r}")
    if len(path) >= 2 and path[1] == ":":
        raise PathError(f"drive path not allowed: {path!r}")
    for part in path.split("/"):
        if part in ("", ".", ".."):
            raise PathError(f"bad path component {part!r} in {path!r}")
    return path


def safe_join(root, rel_path: str) -> Path:
    """Join a checked relative path onto root, refusing anything that lands outside it."""
    check_rel_path(rel_path)
    root = Path(root).resolve()
    target = root.joinpath(*rel_path.split("/")).resolve()
    if target != root and root not in target.parents:
        raise PathError(f"path escapes destination: {rel_path!r}")
    return target
