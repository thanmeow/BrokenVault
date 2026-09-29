"""HTTP calls to the BrokenVault server, retrying network errors a few times."""

import time

import requests

RETRIES = 3
RETRY_DELAY = 0.5  # seconds, multiplied by the attempt number
TIMEOUT = (5, 120)  # (connect, read) seconds


class ClientError(Exception):
    """An error to show the user as-is (exit code 1)."""


class ServerUnreachable(ClientError):
    pass


class ApiError(ClientError):
    """The server answered with an error status."""

    def __init__(self, status: int, message: str, body: dict):
        super().__init__(f"server error {status}: {message}")
        self.status = status
        self.body = body


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        for attempt in range(1, RETRIES + 1):
            try:
                resp = self.session.request(method, self.base_url + path, timeout=TIMEOUT, **kwargs)
                break
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == RETRIES:
                    raise ServerUnreachable(
                        f"cannot reach server at {self.base_url} ({type(e).__name__}). "
                        "Is it running? Start it with: python -m server"
                    ) from None
                time.sleep(RETRY_DELAY * attempt)

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            raise ApiError(resp.status_code, body.get("error") or resp.text, body)
        return resp

    def create_upload(self, manifest: list) -> dict:
        return self._request("POST", "/uploads", json={"manifest": manifest}).json()

    def missing(self, upload_id: str) -> dict:
        return self._request("GET", f"/uploads/{upload_id}/missing").json()

    def put_chunk(self, hash_: str, data: bytes, upload_id: str) -> dict:
        return self._request(
            "PUT",
            f"/chunks/{hash_}",
            params={"upload_id": upload_id},
            data=data,
            headers={"Content-Type": "application/octet-stream"},
        ).json()

    def commit(self, upload_id: str) -> dict:
        return self._request("POST", f"/uploads/{upload_id}/commit").json()

    def versions(self) -> list:
        return self._request("GET", "/versions").json()

    def manifest(self, version_id: str) -> dict:
        return self._request("GET", f"/versions/{version_id}/manifest").json()

    def chunk(self, hash_: str) -> bytes:
        return self._request("GET", f"/chunks/{hash_}").content

    def verify(self) -> dict:
        return self._request("POST", "/verify").json()
