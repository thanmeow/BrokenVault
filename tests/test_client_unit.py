import pytest
import requests

from client import api as api_module
from client import cli as cli_module
from client.api import Api, ApiError, ClientError, ServerUnreachable
from client.backup import Interrupted, run_backup
from client.state import State, manifest_hash, state_key


class FlakySession:
    """Raises ConnectionError for the first `failures` calls, then returns `status`."""

    def __init__(self, failures, status=200, content=b"[]"):
        self.failures = failures
        self.status = status
        self.content = content
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise requests.ConnectionError("connection refused")
        resp = requests.Response()
        resp.status_code = self.status
        resp._content = self.content
        return resp


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(api_module, "RETRY_DELAY", 0)


def test_retries_network_errors_then_succeeds():
    api = Api("http://example.invalid/")
    api.session = FlakySession(failures=api_module.RETRIES - 1)
    assert api.versions() == []
    assert api.session.calls == api_module.RETRIES
    assert api.base_url == "http://example.invalid"


def test_gives_up_with_clear_message():
    api = Api("http://127.0.0.1:1")
    api.session = FlakySession(failures=100)
    with pytest.raises(ServerUnreachable, match="cannot reach server at http://127.0.0.1:1"):
        api.versions()
    assert api.session.calls == api_module.RETRIES


def test_http_errors_are_not_retried():
    api = Api("http://x")
    api.session = FlakySession(failures=0, status=404, content=b'{"error": "unknown version v"}')
    with pytest.raises(ApiError) as exc:
        api.manifest("v")
    assert exc.value.status == 404
    assert "unknown version v" in str(exc.value)
    assert api.session.calls == 1


def test_state_put_get_remove(tmp_path):
    state = State(tmp_path / ".brokenvault" / "state.json")
    key = state_key("/f", "http://s", "abc")
    assert state.get(key) is None
    record = {"folder": "/f", "server": "http://s", "upload_id": "u1"}
    state.put(key, record)
    assert State(state.path).get(key) == record  # persisted to disk
    state.remove(key)
    assert state.get(key) is None


def test_state_forget_folder_only_drops_matching_entries(tmp_path):
    state = State(tmp_path / "state.json")
    state.put("a", {"folder": "/f", "server": "http://s", "upload_id": "1"})
    state.put("b", {"folder": "/g", "server": "http://s", "upload_id": "2"})
    state.forget_folder("/f", "http://s")
    assert state.get("a") is None
    assert state.get("b")["upload_id"] == "2"


def test_corrupt_state_file_gives_clear_error(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(ClientError, match="Delete it"):
        State(path).get("x")


class FakeApi:
    """Accepts the upload, then fails the Nth chunk upload with `error`."""

    base_url = "http://fake"

    def __init__(self, fail_on: int, error: BaseException):
        self.fail_on, self.error, self.puts = fail_on, error, 0

    def create_upload(self, manifest):
        missing = [h for e in manifest for h in e["chunks"]]
        return {"id": "up1", "missing": list(dict.fromkeys(missing))}

    def put_chunk(self, h, data, upload_id):
        self.puts += 1
        if self.puts == self.fail_on:
            raise self.error


@pytest.mark.parametrize(
    "error, reason",
    [(KeyboardInterrupt(), "Ctrl+C"), (ServerUnreachable("cannot reach server"), "cannot reach server")],
)
def test_backup_interruptions_are_resumable(tmp_path, error, reason):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        (src / f"f{i}").write_bytes(f"content {i}".encode())
    state = State(tmp_path / "state.json")

    with pytest.raises(Interrupted) as exc:
        run_backup(FakeApi(fail_on=2, error=error), src, state, log=lambda *_: None)

    assert exc.value.reason == reason
    assert (exc.value.upload_id, exc.value.sent, exc.value.remaining) == ("up1", 1, 2)
    (record,) = state._load().values()
    assert record["upload_id"] == "up1"  # kept, so a rerun resumes


def test_ctrl_c_outside_upload_prints_cancelled(monkeypatch, capsys):
    def boom(api, args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "cmd_list", boom)
    assert cli_module.main(["list"]) == cli_module.EXIT_CANCELLED
    assert "cancelled" in capsys.readouterr().err


def test_manifest_hash_ignores_key_order():
    a = [{"path": "x", "type": "file", "size": 0, "mtime_ns": 1, "chunks": []}]
    b = [{"chunks": [], "mtime_ns": 1, "size": 0, "type": "file", "path": "x"}]
    assert manifest_hash(a) == manifest_hash(b)
    assert manifest_hash(a) != manifest_hash([dict(a[0], mtime_ns=2)])
