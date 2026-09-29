"""Fixed-size chunking and chunk IDs."""

import hashlib
from typing import Iterator

CHUNK_SIZE = 512 * 1024  # 512 KiB

_HEX_DIGITS = set("0123456789abcdef")


def chunk_id(data: bytes) -> str:
    """Chunk ID = lowercase hex SHA-256 of the chunk bytes."""
    return hashlib.sha256(data).hexdigest()


def is_chunk_id(value: object) -> bool:
    """True if value looks like a chunk ID (64 lowercase hex characters)."""
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX_DIGITS


def expected_chunk_count(size: int) -> int:
    """Number of chunks a file of `size` bytes splits into (0 for an empty file)."""
    return (size + CHUNK_SIZE - 1) // CHUNK_SIZE


def iter_file_chunks(path) -> Iterator[bytes]:
    """Yield the file's bytes in CHUNK_SIZE pieces. The last piece may be shorter."""
    with open(path, "rb") as f:
        while True:
            data = f.read(CHUNK_SIZE)
            if not data:
                return
            yield data
