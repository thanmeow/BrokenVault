"""restore: rebuild a completed version into an empty folder, verifying every chunk."""

import os
import tempfile
from pathlib import Path

from common.chunks import chunk_id
from common.manifest import DIR, FILE, ManifestError, manifest_from_list
from common.paths import safe_join
from client.api import ApiError, ClientError
from client.progress import Progress


def run_restore(api, version_id: str, dest, progress_stream=None) -> dict:
    dest = Path(dest)
    _check_destination(dest)

    try:
        version = api.manifest(version_id)
    except ApiError as e:
        if e.status == 404:
            raise ClientError(f"unknown version {version_id} (run 'list' to see versions)") from None
        raise
    try:
        entries = manifest_from_list(version["manifest"])
    except ManifestError as e:
        raise ClientError(f"server sent an invalid manifest: {e}") from None

    dest.mkdir(parents=True, exist_ok=True)
    dirs = [e for e in entries if e.type == DIR]
    files = [e for e in entries if e.type == FILE]

    for d in dirs:
        safe_join(dest, d.path).mkdir(parents=True, exist_ok=True)

    total = sum(f.size for f in files)
    progress = Progress("restoring", sum(len(f.chunks) for f in files), total, progress_stream)
    try:
        for f in files:
            target = safe_join(dest, f.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            _restore_file(api, f, target, dest, progress)
            os.utime(target, ns=(f.mtime_ns, f.mtime_ns))
    finally:
        progress.done()

    # Directory mtimes last (writing files changes them), deepest first.
    for d in sorted(dirs, key=lambda e: e.path.count("/"), reverse=True):
        os.utime(safe_join(dest, d.path), ns=(d.mtime_ns, d.mtime_ns))

    return {"id": version_id, "files": len(files), "dirs": len(dirs), "bytes": total}


def _check_destination(dest: Path) -> None:
    if not dest.exists():
        return
    if not dest.is_dir():
        raise ClientError(f"destination {dest} exists and is not a folder")
    if any(dest.iterdir()):
        raise ClientError(f"destination folder {dest} is not empty; restore needs an empty folder")


def _restore_file(api, entry, target: Path, dest: Path, progress: Progress) -> None:
    """Write the file's chunks in order to a temp file, then rename it into place."""
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".restore-")
    try:
        with os.fdopen(fd, "wb") as out:
            for h in entry.chunks:
                data = _fetch_chunk(api, h, entry.path)
                if chunk_id(data) != h:
                    raise ClientError(
                        f"hash mismatch for chunk {h} in {entry.path}: the server's copy is damaged "
                        f"(run verify). Restore stopped; {dest} is incomplete."
                    )
                out.write(data)
                progress.advance(len(data))
            size = out.tell()
        if size != entry.size:
            raise ClientError(f"{entry.path}: restored {size} bytes, expected {entry.size}")
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _fetch_chunk(api, hash_: str, path: str) -> bytes:
    try:
        return api.chunk(hash_)
    except ApiError as e:
        if e.status == 404:
            raise ClientError(
                f"chunk {hash_} for {path} is missing on the server (run verify)"
            ) from None
        raise
