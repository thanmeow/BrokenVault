"""End-to-end: the real client CLI against a real uvicorn server process."""

import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

from client import api as api_module
from client.cli import main
from common.chunks import CHUNK_SIZE, chunk_id

REPO = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProcess:
    def __init__(self, data_dir: Path, port: int, log_path: Path):
        self.data_dir = data_dir
        self.port = port
        self.log_path = log_path
        self.proc = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "server", "--data-dir", str(self.data_dir), "--port", str(self.port)],
            cwd=REPO,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early:\n{self.log_path.read_text(errors='replace')}")
            try:
                if requests.get(self.url + "/versions", timeout=0.5).ok:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.1)
        raise RuntimeError("server did not start in time")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.log.close()

    def chunk_path(self, data: bytes) -> Path:
        h = chunk_id(data)
        return self.data_dir / "chunks" / h[:2] / h


@dataclass
class Result:
    code: int
    out: str
    err: str


def field(output: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}:\s+(\S+)", output, re.MULTILINE)
    assert m, f"no {name!r} in output:\n{output}"
    return m.group(1)


def chunks_uploaded_this_run(output: str) -> int:
    return int(re.search(r"(\d+) uploaded this run", output).group(1))


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(api_module, "RETRY_DELAY", 0)


@pytest.fixture
def server(tmp_path):
    s = ServerProcess(tmp_path / "vault", free_port(), tmp_path / "server.log")
    s.start()
    yield s
    s.stop()


@pytest.fixture
def cli(server, tmp_path, monkeypatch, capsys):
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)  # the state file goes in ./.brokenvault

    def run(*args) -> Result:
        code = main(["--server", server.url, *[str(a) for a in args]])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return run


def backup_ok(cli, folder, *extra) -> Result:
    r = cli("backup", folder, *extra)
    assert r.code == 0, r.err + r.out
    return r


def make_tree(root: Path) -> None:
    (root / "docs" / "deep").mkdir(parents=True)
    (root / "empty_dir").mkdir()
    (root / "hello.txt").write_bytes(b"hello world\n")
    (root / "docs" / "big.bin").write_bytes(os.urandom(2 * CHUNK_SIZE + 1234))
    (root / "docs" / "deep" / "empty.txt").write_bytes(b"")
    (root / "same1.bin").write_bytes(b"s" * 1000)
    (root / "same2.bin").write_bytes(b"s" * 1000)
    # Distinct old mtimes; deepest first so setting a dir's time comes after its children.
    paths = sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for i, p in enumerate(paths):
        t = 1_600_000_000 + i * 1000
        os.utime(p, (t, t))


def snapshot(root: Path) -> dict:
    result = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        content = p.read_bytes() if p.is_file() else None
        result[rel] = ("file" if p.is_file() else "dir", content, p.stat().st_mtime)
    return result


# --- round trip ------------------------------------------------------------


def test_restore_matches_original(cli, tmp_path):
    src = tmp_path / "src"
    make_tree(src)
    version = field(backup_ok(cli, src).out, "version")

    dest = tmp_path / "restored"
    r = cli("restore", version, dest)
    assert r.code == 0, r.err
    assert "restoring: 6/6 chunks" in r.err  # big.bin has 3, the three small files 1 each

    before, after = snapshot(src), snapshot(dest)
    assert set(after) == set(before)
    for path, (kind, content, mtime) in before.items():
        assert after[path][0] == kind, path
        assert after[path][1] == content, path
        assert abs(after[path][2] - mtime) <= 1, path
    assert after["docs/deep/empty.txt"] == ("file", b"", before["docs/deep/empty.txt"][2])
    assert after["empty_dir"][0] == "dir"


def test_restore_into_existing_empty_folder(cli, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_bytes(b"a")
    version = field(backup_ok(cli, src).out, "version")
    dest = tmp_path / "dest"
    dest.mkdir()
    assert cli("restore", version, dest).code == 0
    assert (dest / "a").read_bytes() == b"a"


# --- dedup -----------------------------------------------------------------


def test_unchanged_folder_uploads_nothing(cli, tmp_path):
    src = tmp_path / "src"
    make_tree(src)
    first = backup_ok(cli, src).out
    second = backup_ok(cli, src).out

    assert int(field(first, "uploaded bytes")) > 0
    assert int(field(second, "uploaded bytes")) == 0
    assert field(second, "reused bytes") == field(first, "uploaded bytes")
    assert field(second, "version") != field(first, "version")
    assert chunks_uploaded_this_run(second) == 0


def test_small_change_in_large_file_uploads_one_chunk(cli, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    big = src / "big.bin"
    big.write_bytes(os.urandom(6 * CHUNK_SIZE))
    backup_ok(cli, src)

    with open(big, "r+b") as f:
        f.seek(2 * CHUNK_SIZE + 100)
        f.write(b"CHANGED!")
    out = backup_ok(cli, src).out

    assert int(field(out, "total bytes")) == 6 * CHUNK_SIZE
    assert int(field(out, "uploaded bytes")) == CHUNK_SIZE
    assert int(field(out, "reused bytes")) == 5 * CHUNK_SIZE
    assert chunks_uploaded_this_run(out) == 1


# --- resume ----------------------------------------------------------------


def test_resume_after_interrupt_and_server_restart(cli, server, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "data.bin").write_bytes(os.urandom(4 * CHUNK_SIZE))

    r = cli("backup", src, "--stop-after", "2")
    assert r.code == 3
    assert "interrupted (--stop-after 2) after 2 chunk upload(s); 2 chunk(s) still missing" in r.out
    assert "uploading: 2/4 chunks, 1.0/2.0 MiB" in r.err
    state = json.loads((Path.cwd() / ".brokenvault" / "state.json").read_text())
    (record,) = state.values()
    upload_id = record["upload_id"]
    assert upload_id not in cli("list").out

    server.stop()
    server.start()  # new process, same data dir and port

    r = backup_ok(cli, src)
    assert f"resuming upload {upload_id}: 2 chunk(s) still missing" in r.out
    assert field(r.out, "version") == upload_id
    assert field(r.out, "state") == "completed"
    assert chunks_uploaded_this_run(r.out) == 2  # the 2 verified chunks weren't re-sent
    assert "uploading: 2/2 chunks, 1.0/1.0 MiB" in r.err
    assert "2 already on server" in r.out
    assert int(field(r.out, "uploaded bytes")) == 4 * CHUNK_SIZE
    assert json.loads((Path.cwd() / ".brokenvault" / "state.json").read_text()) == {}
    assert upload_id in cli("list").out


def test_resume_starts_fresh_if_server_forgot_upload(cli, server, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_bytes(os.urandom(2 * CHUNK_SIZE))
    assert cli("backup", src, "--stop-after", "1").code == 3
    old_id = next(iter(json.loads((Path.cwd() / ".brokenvault" / "state.json").read_text()).values()))[
        "upload_id"
    ]

    # Wipe the server's data: it no longer knows the upload.
    server.stop()
    server.data_dir = tmp_path / "fresh_vault"
    server.start()

    r = backup_ok(cli, src)
    assert f"server no longer knows upload {old_id}" in r.out
    assert field(r.out, "version") != old_id


def test_state_dir_inside_backed_up_folder_is_ignored(cli, server, tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_bytes(os.urandom(2 * CHUNK_SIZE))
    monkeypatch.chdir(src)  # state file now lives inside the folder being backed up

    assert cli("backup", ".", "--stop-after", "1").code == 3
    r = backup_ok(cli, ".")
    assert "resuming upload" in r.out  # the state file didn't change the manifest
    version = field(r.out, "version")
    manifest = requests.get(f"{server.url}/versions/{version}/manifest", timeout=5).json()["manifest"]
    assert [e["path"] for e in manifest] == ["a"]


def test_unfinished_upload_not_listed(cli, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_bytes(os.urandom(2 * CHUNK_SIZE))
    assert cli("backup", src, "--stop-after", "1").code == 3

    r = cli("list")
    assert r.code == 0
    assert "no completed versions" in r.out


def test_list_shows_versions(cli, tmp_path):
    src = tmp_path / "src"
    make_tree(src)
    version = field(backup_ok(cli, src).out, "version")
    r = cli("list")
    assert r.code == 0
    (line,) = [l for l in r.out.splitlines() if l.startswith(version)]
    total = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
    assert line.split()[-2:] == ["5", str(total)]  # file count, total bytes


# --- verify ----------------------------------------------------------------


def test_verify_reports_corrupt_and_deleted_chunks(cli, server, tmp_path):
    shared, only_v1 = os.urandom(1000), os.urandom(1000)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_bytes(shared)
    (src / "b.txt").write_bytes(only_v1)
    v1 = field(backup_ok(cli, src).out, "version")
    (src / "b.txt").unlink()
    (src / "c.txt").write_bytes(b"new in v2")
    v2 = field(backup_ok(cli, src).out, "version")

    r = cli("verify")
    assert r.code == 0
    assert r.out.startswith("OK")

    server.chunk_path(shared).write_bytes(b"bit rot")
    server.chunk_path(only_v1).unlink()
    r = cli("verify")

    assert r.code == 1
    out = r.out
    assert f"CORRUPT chunk {chunk_id(shared)}" in out
    assert f"MISSING chunk {chunk_id(only_v1)}" in out
    assert f"version {v1}: a.txt" in out
    assert f"version {v2}: a.txt" in out
    assert f"version {v1}: b.txt" in out
    assert f"version {v2}: c.txt" not in out
    # Never repaired.
    assert server.chunk_path(shared).read_bytes() == b"bit rot"


def test_restore_stops_on_hash_mismatch(cli, server, tmp_path):
    data = os.urandom(1000)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_bytes(data)
    version = field(backup_ok(cli, src).out, "version")
    server.chunk_path(data).write_bytes(b"tampered")

    dest = tmp_path / "dest"
    r = cli("restore", version, dest)
    assert r.code == 1
    assert "hash mismatch" in r.err
    assert [p for p in dest.rglob("*") if p.is_file()] == []  # no partial or temp file


# --- error messages --------------------------------------------------------


def test_restore_unknown_version(cli, tmp_path):
    r = cli("restore", "doesnotexist", tmp_path / "dest")
    assert r.code == 1
    assert "unknown version doesnotexist" in r.err


def test_restore_refuses_non_empty_destination(cli, tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep.txt").write_bytes(b"mine")
    r = cli("restore", "anything", dest)
    assert r.code == 1
    assert "not empty" in r.err
    assert (dest / "keep.txt").read_bytes() == b"mine"


def test_backup_missing_folder(cli, tmp_path):
    r = cli("backup", tmp_path / "nope")
    assert r.code == 1
    assert "not a folder" in r.err


def test_server_unreachable(capsys):
    code = main(["--server", f"http://127.0.0.1:{free_port()}", "list"])
    assert code == 1
    assert "cannot reach server" in capsys.readouterr().err
