"""HTTP layer: thin FastAPI routes over Vault."""

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.vault import Vault, VaultError


class NewUpload(BaseModel):
    manifest: list


def create_app(data_dir) -> FastAPI:
    vault = Vault(data_dir)
    app = FastAPI(title="BrokenVault")
    app.state.vault = vault

    # Every error is JSON: {"error": "...", ...extra fields}.
    @app.exception_handler(VaultError)
    async def vault_error(request, exc: VaultError):
        return JSONResponse({"error": exc.message, **exc.extra}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc: StarletteHTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc: RequestValidationError):
        return JSONResponse(
            {"error": "invalid request", "details": jsonable_encoder(exc.errors())},
            status_code=422,
        )

    @app.post("/uploads", status_code=201)
    def create_upload(body: NewUpload):
        return vault.create_upload(body.manifest)

    @app.get("/uploads/{upload_id}/missing")
    def get_missing(upload_id: str):
        return vault.get_missing(upload_id)

    @app.post("/uploads/{upload_id}/commit")
    def commit(upload_id: str):
        return vault.commit(upload_id)

    @app.put("/chunks/{hash_}")
    async def put_chunk(hash_: str, request: Request, upload_id: Optional[str] = None):
        data = await request.body()
        return await run_in_threadpool(vault.put_chunk, hash_, data, upload_id)

    @app.get("/chunks/{hash_}")
    def get_chunk(hash_: str):
        return Response(vault.get_chunk(hash_), media_type="application/octet-stream")

    @app.get("/versions")
    def list_versions():
        return vault.list_versions()

    @app.get("/versions/{version_id}/manifest")
    def get_manifest(version_id: str):
        return vault.get_manifest(version_id)

    @app.post("/verify")
    def verify():
        return vault.verify()

    return app
