"""Run independently: python -m uvicorn syncid.app:app --host 127.0.0.1 --port 8001."""
import hashlib
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .crypto import encode
from .engine import IdentityEngine

MAX_BYTES = 25 * 1024 * 1024


def detect_type(head):
    # Byte-derived MIME; unknown content is valid opaque digital content.
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return "application/octet-stream"


async def fingerprint(file):
    h, size, head = hashlib.sha256(), 0, b""
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise HTTPException(413, "Sync ID prototype limit: 25 MiB per file")
            if not head:
                head = chunk[:64]
            h.update(chunk)
    finally:
        await file.close()
    if not size:
        raise HTTPException(400, "File is empty")
    return h.hexdigest(), size, detect_type(head)


def create_app(engine=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = engine or IdentityEngine()
        yield

    app = FastAPI(title="Sync ID — independent identity prototype", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        with app.state.engine.connect() as con:
            con.execute("SELECT 1")
        return {"status": "ok", "service": "syncid", "version": "0.1.0", "protocol": "SyncID/v1",
                "registration_auth": "bearer" if os.environ.get("SYNCID_API_TOKEN") else "local_demo_open"}

    @app.get("/v1/public-key")
    def public_key():
        e = app.state.engine
        return {"alg": "Ed25519", "kid": e.kid, "issuer": e.issuer, "public_key": encode(e.public),
                "trust": "Self-published demo key; authenticate or pin independently."}

    @app.post("/v1/register")
    def register(file: UploadFile = File(...), producer_label: str = Form("", max_length=200),
                 producer_simulated: bool = Form(False), authorization: str | None = Header(None)):
        token = os.environ.get("SYNCID_API_TOKEN")
        if token and not secrets.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401, "A valid registration bearer token is required")
        # Sync endpoint: hashing and SQLite do not block the async event loop.
        h, size, head = hashlib.sha256(), 0, b""
        try:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Sync ID prototype limit: 25 MiB per file")
                if not head:
                    head = chunk[:64]
                h.update(chunk)
        finally:
            file.file.close()
        if not size:
            raise HTTPException(400, "File is empty")
        record, created = app.state.engine.register(h.hexdigest(), size, detect_type(head), producer_label, producer_simulated)
        return JSONResponse(dict(app.state.engine.describe(record), created=created), status_code=201 if created else 200)

    @app.get("/v1/identity/{sync_id}")
    def identity(sync_id: str):
        record = app.state.engine.get(sync_id)
        if not record:
            raise HTTPException(404, "Identity not found in this registry")
        return app.state.engine.describe(record)

    @app.post("/v1/verify")
    async def verify(sync_id: str = Form(..., max_length=100), file: UploadFile = File(...)):
        digest, size, _ = await fingerprint(file)
        record = app.state.engine.get(sync_id)
        if not record:
            raise HTTPException(404, "Identity not found in this registry")
        result = app.state.engine.describe(record)
        match = digest == record["payload"]["sha256"] and size == record["payload"]["size_bytes"]
        return dict(result, calculated_sha256=digest, hash_match=match,
                    content_verified=match and result["signature_valid"],
                    verification_status="match" if match and result["signature_valid"] else "rejected")

    return app


app = create_app()
