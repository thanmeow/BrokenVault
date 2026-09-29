"""Server logic: SQLite metadata plus the on-disk chunk store. No HTTP in here."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from common.chunks import CHUNK_SIZE, chunk_id, is_chunk_id
from common.manifest import FILE, ManifestError, manifest_from_list, manifest_to_list
from server.chunkstore import ChunkStore, HashMismatch

UNFINISHED = "unfinished"
COMPLETED = "completed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    hash      TEXT PRIMARY KEY,
    size      INTEGER NOT NULL,
    stored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id             TEXT PRIMARY KEY,
    state          TEXT NOT NULL CHECK (state IN ('unfinished', 'completed')),
    manifest       TEXT NOT NULL,
    total_bytes    INTEGER NOT NULL,
    uploaded_bytes INTEGER NOT NULL DEFAULT 0,
    reused_bytes   INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    completed_at   TEXT
);
-- Unique chunk IDs each upload needs, so "what's missing" is a simple join.
CREATE TABLE IF NOT EXISTS upload_chunks (
    upload_id TEXT NOT NULL REFERENCES uploads(id),
    hash      TEXT NOT NULL,
    PRIMARY KEY (upload_id, hash)
);
"""


class VaultError(Exception):
    """An error to report to the client, with an HTTP status code and extra JSON fields."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary(row) -> dict:
    return {
        "id": row["id"],
        "state": row["state"],
        "total_bytes": row["total_bytes"],
        "uploaded_bytes": row["uploaded_bytes"],
        "reused_bytes": row["reused_bytes"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


class Vault:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "vault.db"
        self.chunks = ChunkStore(self.data_dir / "chunks")
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # --- database helpers ---------------------------------------------------

    @contextmanager
    def _connect(self):
        # One connection per operation keeps things thread-safe under FastAPI's thread pool.
        # isolation_level=None: we issue BEGIN/COMMIT ourselves.
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self, conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _get_upload(self, conn, upload_id: str):
        row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            raise VaultError(404, f"unknown upload {upload_id}")
        return row

    def _missing(self, conn, upload_id: str) -> list:
        """Chunk IDs the upload needs that the server doesn't have (no row, or file gone)."""
        rows = conn.execute(
            """SELECT uc.hash, c.hash IS NOT NULL AS known
               FROM upload_chunks uc LEFT JOIN chunks c ON c.hash = uc.hash
               WHERE uc.upload_id = ? ORDER BY uc.rowid""",
            (upload_id,),
        ).fetchall()
        return [r["hash"] for r in rows if not r["known"] or not self.chunks.exists(r["hash"])]

    # --- uploads ------------------------------------------------------------

    def create_upload(self, manifest_items) -> dict:
        try:
            entries = manifest_from_list(manifest_items)
        except ManifestError as e:
            raise VaultError(400, f"invalid manifest: {e}") from None

        hashes = list(dict.fromkeys(c for e in entries for c in e.chunks))
        total_bytes = sum(e.size for e in entries if e.type == FILE)
        upload_id = uuid.uuid4().hex

        with self._connect() as conn, self._transaction(conn):
            conn.execute(
                """INSERT INTO uploads (id, state, manifest, total_bytes, reused_bytes, created_at)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                (upload_id, UNFINISHED, json.dumps(manifest_to_list(entries)), total_bytes, _now()),
            )
            conn.executemany(
                "INSERT INTO upload_chunks (upload_id, hash) VALUES (?, ?)",
                [(upload_id, h) for h in hashes],
            )
            # Reused = bytes of chunks the server already has as this upload starts.
            present = conn.execute(
                """SELECT c.hash, c.size FROM upload_chunks uc JOIN chunks c ON c.hash = uc.hash
                   WHERE uc.upload_id = ?""",
                (upload_id,),
            ).fetchall()
            reused = sum(r["size"] for r in present if self.chunks.exists(r["hash"]))
            conn.execute("UPDATE uploads SET reused_bytes = ? WHERE id = ?", (reused, upload_id))
            missing = self._missing(conn, upload_id)
            row = self._get_upload(conn, upload_id)

        return {**_summary(row), "missing": missing}

    def get_missing(self, upload_id: str) -> dict:
        with self._connect() as conn:
            row = self._get_upload(conn, upload_id)
            return {"id": upload_id, "state": row["state"], "missing": self._missing(conn, upload_id)}

    def commit(self, upload_id: str) -> dict:
        """Mark the upload completed in one transaction, but only if every chunk is present."""
        with self._connect() as conn, self._transaction(conn):
            row = self._get_upload(conn, upload_id)
            if row["state"] == COMPLETED:
                return _summary(row)
            missing = self._missing(conn, upload_id)
            if missing:
                raise VaultError(409, f"{len(missing)} chunk(s) still missing", missing=missing)
            conn.execute(
                "UPDATE uploads SET state = ?, completed_at = ? WHERE id = ?",
                (COMPLETED, _now(), upload_id),
            )
            return _summary(self._get_upload(conn, upload_id))

    # --- chunks -------------------------------------------------------------

    def put_chunk(self, hash_: str, data: bytes, upload_id=None) -> dict:
        """Store one chunk. Sending a chunk the server already has is a harmless no-op.

        With upload_id, the bytes count towards that upload's uploaded_bytes (only when
        the server actually had to store them).
        """
        _check_hash(hash_)
        if not data:
            raise VaultError(400, "empty chunk")
        if len(data) > CHUNK_SIZE:
            raise VaultError(413, f"chunk larger than {CHUNK_SIZE} bytes")

        with self._connect() as conn:
            if upload_id is not None:
                row = self._get_upload(conn, upload_id)
                if row["state"] != UNFINISHED:
                    raise VaultError(409, f"upload {upload_id} is already completed")
                needed = conn.execute(
                    "SELECT 1 FROM upload_chunks WHERE upload_id = ? AND hash = ?",
                    (upload_id, hash_),
                ).fetchone()
                if needed is None:
                    raise VaultError(400, f"chunk {hash_} is not part of upload {upload_id}")

            known = conn.execute("SELECT 1 FROM chunks WHERE hash = ?", (hash_,)).fetchone()
            if known and self.chunks.exists(hash_):
                return {"hash": hash_, "stored": False}

            try:
                self.chunks.write(hash_, data)
            except HashMismatch as e:
                raise VaultError(400, str(e)) from None

            with self._transaction(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO chunks (hash, size, stored_at) VALUES (?, ?, ?)",
                    (hash_, len(data), _now()),
                )
                if upload_id is not None:
                    conn.execute(
                        "UPDATE uploads SET uploaded_bytes = uploaded_bytes + ? WHERE id = ?",
                        (len(data), upload_id),
                    )
        return {"hash": hash_, "stored": True}

    def get_chunk(self, hash_: str) -> bytes:
        _check_hash(hash_)
        with self._connect() as conn:
            known = conn.execute("SELECT 1 FROM chunks WHERE hash = ?", (hash_,)).fetchone()
        if not known or not self.chunks.exists(hash_):
            raise VaultError(404, f"unknown chunk {hash_}")
        return self.chunks.read(hash_)

    # --- versions -----------------------------------------------------------

    def list_versions(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM uploads WHERE state = ? ORDER BY completed_at, rowid", (COMPLETED,)
            ).fetchall()
        return [_summary(r) for r in rows]

    def get_manifest(self, version_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM uploads WHERE id = ? AND state = ?", (version_id, COMPLETED)
            ).fetchone()
        if row is None:
            raise VaultError(404, f"unknown version {version_id}")
        return {**_summary(row), "manifest": json.loads(row["manifest"])}

    # --- verify -------------------------------------------------------------

    def verify(self) -> dict:
        """Re-hash every chunk used by a completed version. Reports problems; never repairs."""
        with self._connect() as conn:
            versions = conn.execute(
                "SELECT id, manifest FROM uploads WHERE state = ? ORDER BY completed_at, rowid",
                (COMPLETED,),
            ).fetchall()
            hashes = [
                r["hash"]
                for r in conn.execute(
                    """SELECT DISTINCT uc.hash FROM upload_chunks uc
                       JOIN uploads u ON u.id = uc.upload_id WHERE u.state = ?""",
                    (COMPLETED,),
                )
            ]

        problems = {}
        for h in hashes:
            if not self.chunks.exists(h):
                problems[h] = "missing"
            elif chunk_id(self.chunks.read(h)) != h:
                problems[h] = "corrupt"

        affected = {h: [] for h in problems}
        for v in versions:
            for entry in json.loads(v["manifest"]):
                for h in dict.fromkeys(entry["chunks"]):
                    if h in affected:
                        affected[h].append({"version_id": v["id"], "path": entry["path"]})

        affected_versions = list(
            dict.fromkeys(a["version_id"] for items in affected.values() for a in items)
        )
        return {
            "ok": not problems,
            "checked_chunks": len(hashes),
            "problems": [
                {"hash": h, "problem": p, "affected": affected[h]} for h, p in problems.items()
            ],
            "affected_versions": affected_versions,
        }


def _check_hash(hash_: str) -> None:
    if not is_chunk_id(hash_):
        raise VaultError(400, f"invalid chunk ID {hash_!r}")
