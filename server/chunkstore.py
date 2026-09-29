"""Content-addressed chunk files on disk: <root>/ab/<hash>."""

import os
import tempfile
from pathlib import Path

from common.chunks import chunk_id


class HashMismatch(ValueError):
    """Chunk bytes don't hash to the claimed chunk ID."""


class ChunkStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, hash_: str) -> Path:
        return self.root / hash_[:2] / hash_

    def exists(self, hash_: str) -> bool:
        return self.path_for(hash_).is_file()

    def write(self, hash_: str, data: bytes) -> None:
        """Store a chunk: temp file in the same folder -> fsync -> re-hash -> os.replace."""
        if chunk_id(data) != hash_:
            raise HashMismatch(f"received bytes do not hash to {hash_}")

        final = self.path_for(hash_)
        final.parent.mkdir(exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=final.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            # Re-hash what actually landed on disk before making it visible.
            if chunk_id(Path(tmp_name).read_bytes()) != hash_:
                raise HashMismatch(f"chunk {hash_} changed while being written")
            os.replace(tmp_name, final)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def read(self, hash_: str) -> bytes:
        return self.path_for(hash_).read_bytes()
