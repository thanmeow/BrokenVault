"""backup: scan a folder, upload only the chunks the server lacks, then commit."""

from datetime import datetime, timezone
from pathlib import Path

from common.chunks import CHUNK_SIZE, chunk_id
from common.manifest import DIR, FILE, manifest_to_list, scan_folder
from common.paths import safe_join
from client.api import ApiError, ClientError
from client.state import manifest_hash, state_key


class Interrupted(Exception):
    """Backup stopped on purpose (--stop-after) before all chunks were sent."""

    def __init__(self, upload_id: str, sent: int, remaining: int):
        super().__init__(upload_id)
        self.upload_id = upload_id
        self.sent = sent
        self.remaining = remaining


def run_backup(api, folder, state, stop_after=None, log=print) -> dict:
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ClientError(f"not a folder: {folder}")

    entries = _exclude_state_dir(scan_folder(folder), folder, state.path.parent)
    manifest = manifest_to_list(entries)
    key = state_key(str(folder), api.base_url, manifest_hash(manifest))

    upload_id, missing, resumed = _resume(api, state, key, log)
    if upload_id is None:
        created = api.create_upload(manifest)
        upload_id, missing = created["id"], created["missing"]
        state.forget_folder(str(folder), api.base_url)
        state.put(
            key,
            {
                "folder": str(folder),
                "server": api.base_url,
                "manifest_hash": manifest_hash(manifest),
                "upload_id": upload_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    locations = _chunk_locations(entries)
    sent = 0
    for h in missing:
        if stop_after is not None and sent >= stop_after:
            raise Interrupted(upload_id, sent, len(missing) - sent)
        path, offset = locations[h]
        data = _read_chunk(folder, path, offset)
        if chunk_id(data) != h:
            raise ClientError(f"{path} changed while backing up; run backup again")
        api.put_chunk(h, data, upload_id)
        sent += 1

    summary = api.commit(upload_id)
    state.remove(key)
    return {
        **summary,
        "resumed": resumed,
        "files": sum(1 for e in entries if e.type == FILE),
        "dirs": sum(1 for e in entries if e.type == DIR),
        "unique_chunks": len(locations),
        "chunks_already_on_server": len(locations) - len(missing),
        "chunks_uploaded": sent,
    }


def _resume(api, state, key, log):
    """Return (upload_id, missing, True) for a saved unfinished upload, else (None, None, False)."""
    saved = state.get(key)
    if saved is None:
        return None, None, False
    upload_id = saved["upload_id"]
    try:
        missing = api.missing(upload_id)["missing"]
    except ApiError as e:
        if e.status != 404:
            raise
        log(f"server no longer knows upload {upload_id}; starting a new upload")
        state.remove(key)
        return None, None, False
    log(f"resuming upload {upload_id}: {len(missing)} chunk(s) still missing")
    return upload_id, missing, True


def _chunk_locations(entries) -> dict:
    """chunk ID -> (file path, byte offset) of its first occurrence."""
    locations = {}
    for e in entries:
        for i, h in enumerate(e.chunks):
            locations.setdefault(h, (e.path, i * CHUNK_SIZE))
    return locations


def _read_chunk(folder: Path, rel_path: str, offset: int) -> bytes:
    with open(safe_join(folder, rel_path), "rb") as f:
        f.seek(offset)
        return f.read(CHUNK_SIZE)


def _exclude_state_dir(entries, folder: Path, state_dir: Path):
    """Don't back up our own state folder if it lives inside the folder being backed up."""
    try:
        rel = state_dir.resolve().relative_to(folder).as_posix()
    except ValueError:
        return entries
    return [e for e in entries if e.path != rel and not e.path.startswith(rel + "/")]
