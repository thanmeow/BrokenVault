"""Manifests: the list of files and directories that make up one backup version."""

import os
from dataclasses import dataclass
from pathlib import Path

from common.chunks import chunk_id, expected_chunk_count, is_chunk_id, iter_file_chunks
from common.paths import PathError, check_rel_path

FILE = "file"
DIR = "dir"


class ManifestError(ValueError):
    """A manifest is malformed or unsafe."""


@dataclass(frozen=True)
class Entry:
    path: str  # relative, '/' separators
    type: str  # FILE or DIR
    size: int  # bytes; always 0 for DIR
    mtime_ns: int  # modification time in nanoseconds since the epoch
    chunks: tuple = ()  # ordered chunk IDs; empty for DIR and for empty files

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "type": self.type,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "chunks": list(self.chunks),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        try:
            return cls(
                path=d["path"],
                type=d["type"],
                size=d["size"],
                mtime_ns=d["mtime_ns"],
                chunks=tuple(d.get("chunks", ())),
            )
        except (KeyError, TypeError) as e:
            raise ManifestError(f"bad manifest entry {d!r}: {e}") from None


def validate_manifest(entries: list) -> None:
    """Raise ManifestError unless every entry is well-formed and paths are unique and safe."""
    types = {}
    for e in entries:
        try:
            check_rel_path(e.path)
        except PathError as err:
            raise ManifestError(str(err)) from None
        if e.path in types:
            raise ManifestError(f"duplicate path: {e.path!r}")
        if e.type not in (FILE, DIR):
            raise ManifestError(f"unknown type {e.type!r} for {e.path!r}")
        if not _is_int(e.size) or e.size < 0:
            raise ManifestError(f"bad size {e.size!r} for {e.path!r}")
        if not _is_int(e.mtime_ns):
            raise ManifestError(f"bad mtime {e.mtime_ns!r} for {e.path!r}")
        if e.type == DIR and (e.size != 0 or e.chunks):
            raise ManifestError(f"directory {e.path!r} must have size 0 and no chunks")
        if e.type == FILE:
            if len(e.chunks) != expected_chunk_count(e.size):
                raise ManifestError(
                    f"{e.path!r}: size {e.size} needs {expected_chunk_count(e.size)} "
                    f"chunks, got {len(e.chunks)}"
                )
            for c in e.chunks:
                if not is_chunk_id(c):
                    raise ManifestError(f"bad chunk ID {c!r} in {e.path!r}")
        types[e.path] = e.type

    # A file can't also be a parent directory ("a" as a file and "a/b" both present).
    for path in types:
        parts = path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if types.get(parent) == FILE:
                raise ManifestError(f"{path!r} is inside file {parent!r}")


def manifest_to_list(entries: list) -> list:
    return [e.to_dict() for e in entries]


def manifest_from_list(items: list) -> list:
    """Parse and validate a manifest received as JSON-style data."""
    if not isinstance(items, list):
        raise ManifestError("manifest must be a list")
    entries = []
    for d in items:
        if not isinstance(d, dict):
            raise ManifestError(f"manifest entry must be an object: {d!r}")
        entries.append(Entry.from_dict(d))
    validate_manifest(entries)
    return entries


def scan_folder(root) -> list:
    """Walk `root` and build a manifest: every file (with chunk IDs) and every directory.

    Symlinks are skipped. Entries are ordered depth-first by name.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    entries = []
    _scan_dir(root, "", entries)
    validate_manifest(entries)
    return entries


def _scan_dir(dir_path, prefix: str, entries: list) -> None:
    with os.scandir(dir_path) as it:
        items = sorted(it, key=lambda item: item.name)
    for item in items:
        if item.is_symlink():
            continue
        rel = prefix + item.name
        mtime_ns = item.stat().st_mtime_ns
        if item.is_dir():
            entries.append(Entry(rel, DIR, 0, mtime_ns))
            _scan_dir(item.path, rel + "/", entries)
        elif item.is_file():
            ids = []
            size = 0
            for data in iter_file_chunks(item.path):
                ids.append(chunk_id(data))
                size += len(data)
            entries.append(Entry(rel, FILE, size, mtime_ns, tuple(ids)))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
