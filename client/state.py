"""The client state file: unfinished upload IDs, so an interrupted backup can resume.

Lives in .brokenvault/state.json under the current directory. Entries are keyed by
absolute folder path + server URL + manifest hash, and removed once the upload commits.
"""

import hashlib
import json
import os
from pathlib import Path

from client.api import ClientError

STATE_DIR = ".brokenvault"
STATE_FILE = "state.json"


def manifest_hash(manifest: list) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def state_key(folder: str, server: str, manifest_hash_: str) -> str:
    return f"{folder}|{server}|{manifest_hash_}"


class State:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path.cwd() / STATE_DIR / STATE_FILE

    def get(self, key: str):
        return self._load().get(key)

    def put(self, key: str, record: dict) -> None:
        data = self._load()
        data[key] = record
        self._save(data)

    def remove(self, key: str) -> None:
        data = self._load()
        if data.pop(key, None) is not None:
            self._save(data)

    def forget_folder(self, folder: str, server: str) -> None:
        """Drop stale entries for this folder+server (from an older version of its contents)."""
        data = self._load()
        kept = {k: r for k, r in data.items() if (r["folder"], r["server"]) != (folder, server)}
        if kept != data:
            self._save(kept)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise ClientError(f"cannot read state file {self.path}: {e}. Delete it to start over.")
        if not isinstance(data, dict):
            raise ClientError(f"state file {self.path} is malformed. Delete it to start over.")
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
