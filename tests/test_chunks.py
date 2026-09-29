import hashlib

from common.chunks import (
    CHUNK_SIZE,
    chunk_id,
    expected_chunk_count,
    is_chunk_id,
    iter_file_chunks,
)


def test_chunk_size_is_512_kib():
    assert CHUNK_SIZE == 524288


def test_chunk_id_is_lowercase_sha256():
    data = b"hello world"
    assert chunk_id(data) == hashlib.sha256(data).hexdigest()
    assert chunk_id(data) == chunk_id(data).lower()
    assert is_chunk_id(chunk_id(data))


def test_is_chunk_id_rejects_bad_values():
    good = chunk_id(b"x")
    assert not is_chunk_id(good.upper())
    assert not is_chunk_id(good[:-1])
    assert not is_chunk_id(good + "0")
    assert not is_chunk_id("g" * 64)
    assert not is_chunk_id(None)


def test_expected_chunk_count():
    assert expected_chunk_count(0) == 0
    assert expected_chunk_count(1) == 1
    assert expected_chunk_count(CHUNK_SIZE) == 1
    assert expected_chunk_count(CHUNK_SIZE + 1) == 2
    assert expected_chunk_count(3 * CHUNK_SIZE) == 3


def test_empty_file_has_no_chunks(tmp_path):
    p = tmp_path / "empty"
    p.write_bytes(b"")
    assert list(iter_file_chunks(p)) == []


def test_file_splits_on_chunk_boundaries(tmp_path):
    data = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE + b"c" * 10
    p = tmp_path / "f"
    p.write_bytes(data)
    chunks = list(iter_file_chunks(p))
    assert [len(c) for c in chunks] == [CHUNK_SIZE, CHUNK_SIZE, 10]
    assert b"".join(chunks) == data


def test_exact_multiple_has_no_trailing_empty_chunk(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"x" * (2 * CHUNK_SIZE))
    assert [len(c) for c in iter_file_chunks(p)] == [CHUNK_SIZE, CHUNK_SIZE]
