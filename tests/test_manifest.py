import os

import pytest

from common.chunks import CHUNK_SIZE, chunk_id
from common.manifest import (
    DIR,
    FILE,
    Entry,
    ManifestError,
    manifest_from_list,
    manifest_to_list,
    scan_folder,
    validate_manifest,
)

ID_A = chunk_id(b"a")
ID_B = chunk_id(b"b")


def by_path(entries):
    return {e.path: e for e in entries}


# --- scan_folder -----------------------------------------------------------


def test_scan_records_files_dirs_empty_files_and_empty_dirs(tmp_path):
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "emptydir").mkdir()
    (tmp_path / "hello.txt").write_bytes(b"hello")
    (tmp_path / "sub" / "empty.bin").write_bytes(b"")
    (tmp_path / "sub" / "deep" / "x").write_bytes(b"xyz")

    entries = by_path(scan_folder(tmp_path))

    assert set(entries) == {
        "emptydir",
        "hello.txt",
        "sub",
        "sub/deep",
        "sub/deep/x",
        "sub/empty.bin",
    }
    assert entries["emptydir"].type == DIR
    assert entries["emptydir"].chunks == ()
    assert entries["hello.txt"] == Entry(
        "hello.txt", FILE, 5, entries["hello.txt"].mtime_ns, (chunk_id(b"hello"),)
    )
    assert entries["sub/empty.bin"].type == FILE
    assert entries["sub/empty.bin"].size == 0
    assert entries["sub/empty.bin"].chunks == ()


def test_scan_uses_forward_slashes(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.txt").write_bytes(b"c")
    paths = [e.path for e in scan_folder(tmp_path)]
    assert "a/b/c.txt" in paths
    assert not any("\\" in p for p in paths)


def test_scan_chunks_large_file_in_order(tmp_path):
    part1 = b"1" * CHUNK_SIZE
    part2 = b"2" * CHUNK_SIZE
    part3 = b"3" * 100
    (tmp_path / "big").write_bytes(part1 + part2 + part3)

    (entry,) = scan_folder(tmp_path)

    assert entry.size == 2 * CHUNK_SIZE + 100
    assert entry.chunks == (chunk_id(part1), chunk_id(part2), chunk_id(part3))


def test_scan_repeated_content_gives_repeated_ids(tmp_path):
    (tmp_path / "f").write_bytes(b"z" * (2 * CHUNK_SIZE))
    (entry,) = scan_folder(tmp_path)
    assert entry.chunks[0] == entry.chunks[1]


def test_scan_records_mtime_ns(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"data")
    mtime_ns = 1_600_000_000_123_456_700
    os.utime(p, ns=(mtime_ns, mtime_ns))
    (entry,) = scan_folder(tmp_path)
    assert entry.mtime_ns == p.stat().st_mtime_ns


def test_scan_empty_folder(tmp_path):
    assert scan_folder(tmp_path) == []


def test_scan_rejects_non_directory(tmp_path):
    p = tmp_path / "file"
    p.write_bytes(b"")
    with pytest.raises(NotADirectoryError):
        scan_folder(p)


# --- validate_manifest -----------------------------------------------------


def test_valid_manifest_passes():
    validate_manifest(
        [
            Entry("d", DIR, 0, 1),
            Entry("d/f", FILE, 1, 1, (ID_A,)),
            Entry("empty", FILE, 0, 1),
        ]
    )


@pytest.mark.parametrize("path", ["/abs", "../up", "a/../../up", "C:/x", "a\\b", ""])
def test_rejects_unsafe_paths(path):
    with pytest.raises(ManifestError):
        validate_manifest([Entry(path, FILE, 0, 1)])


def test_rejects_duplicate_paths():
    with pytest.raises(ManifestError, match="duplicate"):
        validate_manifest([Entry("a", FILE, 0, 1), Entry("a", FILE, 0, 2)])


def test_rejects_duplicate_path_with_different_type():
    with pytest.raises(ManifestError, match="duplicate"):
        validate_manifest([Entry("a", DIR, 0, 1), Entry("a", FILE, 0, 1)])


def test_rejects_file_used_as_parent():
    with pytest.raises(ManifestError, match="inside file"):
        validate_manifest([Entry("a", FILE, 0, 1), Entry("a/b", FILE, 0, 1)])


def test_rejects_unknown_type():
    with pytest.raises(ManifestError):
        validate_manifest([Entry("a", "symlink", 0, 1)])


def test_rejects_dir_with_chunks_or_size():
    with pytest.raises(ManifestError):
        validate_manifest([Entry("d", DIR, 0, 1, (ID_A,))])
    with pytest.raises(ManifestError):
        validate_manifest([Entry("d", DIR, 5, 1)])


def test_rejects_chunk_count_not_matching_size():
    with pytest.raises(ManifestError):
        validate_manifest([Entry("f", FILE, 0, 1, (ID_A,))])
    with pytest.raises(ManifestError):
        validate_manifest([Entry("f", FILE, CHUNK_SIZE + 1, 1, (ID_A,))])


def test_rejects_bad_chunk_id():
    with pytest.raises(ManifestError):
        validate_manifest([Entry("f", FILE, 1, 1, ("NOT-A-HASH",))])


@pytest.mark.parametrize("size", [-1, 1.5, "1", True])
def test_rejects_bad_size(size):
    with pytest.raises(ManifestError):
        validate_manifest([Entry("f", FILE, size, 1)])


# --- serialisation ---------------------------------------------------------


def test_round_trip_through_list(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_bytes(b"x" * (CHUNK_SIZE + 3))
    (tmp_path / "e").write_bytes(b"")
    entries = scan_folder(tmp_path)
    assert manifest_from_list(manifest_to_list(entries)) == entries


def test_from_list_validates():
    items = [
        {"path": "a", "type": FILE, "size": 1, "mtime_ns": 1, "chunks": [ID_A]},
        {"path": "a", "type": FILE, "size": 1, "mtime_ns": 1, "chunks": [ID_B]},
    ]
    with pytest.raises(ManifestError, match="duplicate"):
        manifest_from_list(items)


@pytest.mark.parametrize("items", [{"path": "a"}, [1], [{"path": "a"}], "nope"])
def test_from_list_rejects_malformed(items):
    with pytest.raises(ManifestError):
        manifest_from_list(items)
