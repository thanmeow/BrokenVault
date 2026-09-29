import sqlite3

import pytest
from fastapi.testclient import TestClient

from common.chunks import CHUNK_SIZE, chunk_id
from server.__main__ import parse_args
from server.app import create_app

# --- helpers ---------------------------------------------------------------


def split(data: bytes) -> list:
    return [data[i : i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]


def file_entry(path: str, data: bytes) -> dict:
    return {
        "path": path,
        "type": "file",
        "size": len(data),
        "mtime_ns": 1_700_000_000_000_000_000,
        "chunks": [chunk_id(c) for c in split(data)],
    }


def dir_entry(path: str) -> dict:
    return {"path": path, "type": "dir", "size": 0, "mtime_ns": 1, "chunks": []}


def chunk_map(files: dict) -> dict:
    return {chunk_id(c): c for data in files.values() for c in split(data)}


def start(client, files: dict):
    r = client.post("/uploads", json={"manifest": [file_entry(p, d) for p, d in files.items()]})
    assert r.status_code == 201, r.text
    return r.json()


def put(client, data: bytes, upload_id=None):
    params = {"upload_id": upload_id} if upload_id else {}
    return client.put(f"/chunks/{chunk_id(data)}", content=data, params=params)


def backup(client, files: dict) -> dict:
    """Full happy-path backup; returns the commit response."""
    up = start(client, files)
    chunks = chunk_map(files)
    for h in up["missing"]:
        assert put(client, chunks[h], up["id"]).status_code == 200
    r = client.post(f"/uploads/{up['id']}/commit")
    assert r.status_code == 200, r.text
    return r.json()


def stored_chunk_files(data_dir) -> list:
    return [p for p in (data_dir / "chunks").rglob("*") if p.is_file()]


def db_count(data_dir, sql: str) -> int:
    with sqlite3.connect(data_dir / "vault.db") as conn:
        return conn.execute(sql).fetchone()[0]


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "vault"


@pytest.fixture
def client(data_dir):
    return TestClient(create_app(data_dir))


# --- setup -----------------------------------------------------------------


def test_database_uses_wal(client, data_dir):
    with sqlite3.connect(data_dir / "vault.db") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_cli_defaults():
    args = parse_args([])
    assert args.data_dir == "./vault_data"
    assert args.port == 8000
    args = parse_args(["--data-dir", "x", "--port", "9001"])
    assert (args.data_dir, args.port) == ("x", 9001)


# --- dedup and byte counters -----------------------------------------------


def test_identical_chunks_stored_once(client, data_dir):
    same = b"same content"
    up = start(client, {"a.txt": same, "b.txt": same})
    assert up["missing"] == [chunk_id(same)]  # unique hashes only

    put(client, same, up["id"])
    put(client, same, up["id"])  # re-send is harmless
    client.post(f"/uploads/{up['id']}/commit")

    files = stored_chunk_files(data_dir)
    assert len(files) == 1
    assert files[0].name == chunk_id(same)
    assert files[0].parent.name == chunk_id(same)[:2]
    assert db_count(data_dir, "SELECT COUNT(*) FROM chunks") == 1


def test_byte_counters(client):
    first = backup(client, {"a.txt": b"aaaa", "b.txt": b"aaaa"})
    assert first["total_bytes"] == 8
    assert first["uploaded_bytes"] == 4
    assert first["reused_bytes"] == 0

    second = backup(client, {"a.txt": b"aaaa", "new.txt": b"nn"})
    assert second["total_bytes"] == 6
    assert second["uploaded_bytes"] == 2
    assert second["reused_bytes"] == 4


def test_resend_does_not_double_count(client):
    up = start(client, {"a": b"xyz"})
    assert put(client, b"xyz", up["id"]).json() == {"hash": chunk_id(b"xyz"), "stored": True}
    assert put(client, b"xyz", up["id"]).json() == {"hash": chunk_id(b"xyz"), "stored": False}
    assert client.post(f"/uploads/{up['id']}/commit").json()["uploaded_bytes"] == 3


def test_multi_chunk_file(client, data_dir):
    data = b"A" * CHUNK_SIZE + b"B" * 10
    result = backup(client, {"big.bin": data})
    assert result["uploaded_bytes"] == len(data)
    assert len(stored_chunk_files(data_dir)) == 2


# --- chunk upload checks ---------------------------------------------------


def test_wrong_hash_rejected(client, data_dir):
    claimed = chunk_id(b"what I claim")
    r = client.put(f"/chunks/{claimed}", content=b"something else")
    assert r.status_code == 400
    assert "error" in r.json()
    assert stored_chunk_files(data_dir) == []  # no temp file left behind either
    assert client.get(f"/chunks/{claimed}").status_code == 404


@pytest.mark.parametrize("bad", ["abc", "G" * 64, chunk_id(b"x").upper()])
def test_invalid_chunk_id_rejected(client, bad):
    assert client.put(f"/chunks/{bad}", content=b"x").status_code == 400
    assert client.get(f"/chunks/{bad}").status_code == 400


def test_empty_and_oversize_chunks_rejected(client):
    assert client.put(f"/chunks/{chunk_id(b'')}", content=b"").status_code == 400
    big = b"x" * (CHUNK_SIZE + 1)
    assert client.put(f"/chunks/{chunk_id(big)}", content=big).status_code == 413


def test_chunk_not_in_upload_rejected(client):
    up = start(client, {"a": b"a"})
    assert put(client, b"other", up["id"]).status_code == 400


def test_put_to_completed_or_unknown_upload(client):
    done = backup(client, {"a": b"a"})
    assert put(client, b"a", done["id"]).status_code == 409
    assert put(client, b"a", "nope").status_code == 404


def test_get_chunk_returns_bytes(client):
    backup(client, {"a": b"hello"})
    r = client.get(f"/chunks/{chunk_id(b'hello')}")
    assert r.status_code == 200
    assert r.content == b"hello"


# --- commit ----------------------------------------------------------------


def test_commit_refused_while_chunks_missing(client):
    files = {"a": b"one", "b": b"two"}
    up = start(client, files)
    put(client, b"one", up["id"])

    r = client.post(f"/uploads/{up['id']}/commit")
    assert r.status_code == 409
    assert r.json()["missing"] == [chunk_id(b"two")]
    assert client.get(f"/uploads/{up['id']}/missing").json()["state"] == "unfinished"

    put(client, b"two", up["id"])
    r = client.post(f"/uploads/{up['id']}/commit")
    assert r.status_code == 200
    assert r.json()["state"] == "completed"


def test_commit_twice_returns_same_result(client):
    first = backup(client, {"a": b"a"})
    again = client.post(f"/uploads/{first['id']}/commit")
    assert again.status_code == 200
    assert again.json() == first


def test_empty_files_and_dirs_commit_without_chunks(client):
    manifest = [dir_entry("empty_dir"), file_entry("empty.txt", b"")]
    up = client.post("/uploads", json={"manifest": manifest}).json()
    assert up["missing"] == []
    assert client.post(f"/uploads/{up['id']}/commit").json()["state"] == "completed"
    got = client.get(f"/versions/{up['id']}/manifest").json()["manifest"]
    assert {e["path"] for e in got} == {"empty_dir", "empty.txt"}


def test_unknown_upload_is_404(client):
    assert client.get("/uploads/nope/missing").status_code == 404
    assert client.post("/uploads/nope/commit").status_code == 404


# --- versions --------------------------------------------------------------


def test_unfinished_upload_hidden(client):
    up = start(client, {"a": b"a"})
    assert client.get("/versions").json() == []
    assert client.get(f"/versions/{up['id']}/manifest").status_code == 404

    done = backup(client, {"b": b"b"})
    assert [v["id"] for v in client.get("/versions").json()] == [done["id"]]


def test_manifest_round_trip(client):
    files = {"dir/a.txt": b"aaa", "b.txt": b"bb"}
    done = backup(client, files)
    r = client.get(f"/versions/{done['id']}/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == done["id"]
    assert sorted(e["path"] for e in body["manifest"]) == ["b.txt", "dir/a.txt"]


# --- restart ---------------------------------------------------------------


def test_state_survives_recreating_app(data_dir):
    files = {"a": b"first", "b": b"second"}
    app1 = TestClient(create_app(data_dir))
    up = start(app1, files)
    put(app1, b"first", up["id"])

    app2 = TestClient(create_app(data_dir))  # "restart" the server
    missing = app2.get(f"/uploads/{up['id']}/missing").json()
    assert missing == {"id": up["id"], "state": "unfinished", "missing": [chunk_id(b"second")]}
    put(app2, b"second", up["id"])
    assert app2.post(f"/uploads/{up['id']}/commit").status_code == 200

    app3 = TestClient(create_app(data_dir))
    versions = app3.get("/versions").json()
    assert [v["id"] for v in versions] == [up["id"]]
    assert versions[0]["uploaded_bytes"] == len(b"first") + len(b"second")
    assert app3.get(f"/chunks/{chunk_id(b'first')}").content == b"first"


# --- manifest validation ---------------------------------------------------


@pytest.mark.parametrize(
    "manifest",
    [
        [file_entry("/etc/passwd", b"x")],
        [file_entry("../escape", b"x")],
        [file_entry("a/../../escape", b"x")],
        [file_entry("C:/Windows/x", b"x")],
        [file_entry("a\\b", b"x")],
        [file_entry("dup", b"x"), file_entry("dup", b"y")],
        [{"path": "a", "type": "file"}],
        ["not an object"],
    ],
)
def test_bad_manifest_rejected(client, data_dir, manifest):
    r = client.post("/uploads", json={"manifest": manifest})
    assert r.status_code == 400
    assert "invalid manifest" in r.json()["error"]
    assert db_count(data_dir, "SELECT COUNT(*) FROM uploads") == 0


def test_missing_manifest_field_is_json_error(client):
    r = client.post("/uploads", json={})
    assert r.status_code == 422
    assert "error" in r.json()


# --- verify ----------------------------------------------------------------


def test_verify_clean(client):
    backup(client, {"a": b"a"})
    report = client.post("/verify").json()
    assert report["ok"] is True
    assert report["checked_chunks"] == 1
    assert report["problems"] == []


def test_verify_reports_corrupt_and_deleted_chunks(client, data_dir):
    shared, only_v1, only_v2 = b"shared chunk", b"only in v1", b"only in v2"
    v1 = backup(client, {"a.txt": shared, "b.txt": only_v1})["id"]
    v2 = backup(client, {"a.txt": shared, "sub/c.txt": only_v2, "copy.txt": shared})["id"]
    unfinished = start(client, {"z": shared})  # must not show up in the report

    store = data_dir / "chunks"
    corrupt_path = store / chunk_id(shared)[:2] / chunk_id(shared)
    corrupt_path.write_bytes(b"bit rot")
    deleted_path = store / chunk_id(only_v1)[:2] / chunk_id(only_v1)
    deleted_path.unlink()

    report = client.post("/verify").json()

    assert report["ok"] is False
    assert report["checked_chunks"] == 3
    problems = {p["hash"]: p for p in report["problems"]}
    assert set(problems) == {chunk_id(shared), chunk_id(only_v1)}

    corrupt = problems[chunk_id(shared)]
    assert corrupt["problem"] == "corrupt"
    assert sorted((a["version_id"], a["path"]) for a in corrupt["affected"]) == sorted(
        [(v1, "a.txt"), (v2, "a.txt"), (v2, "copy.txt")]
    )

    deleted = problems[chunk_id(only_v1)]
    assert deleted["problem"] == "missing"
    assert deleted["affected"] == [{"version_id": v1, "path": "b.txt"}]

    assert sorted(report["affected_versions"]) == sorted([v1, v2])
    assert unfinished["id"] not in report["affected_versions"]

    # Never repairs: the corrupt file is untouched and the deleted one stays gone.
    assert corrupt_path.read_bytes() == b"bit rot"
    assert not deleted_path.exists()
