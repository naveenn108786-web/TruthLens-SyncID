"""TruthLens web app (FastAPI). Run:  uvicorn truthlens.app:app --port 8000"""
import os
import logging

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import llm, registry, db
from .validation import ValidationError
from .forensics.c2pa_check import HAVE_C2PA
from .pipeline import ROOT, case_dir, list_cases, load_case, run_case
from .pdf import build_pdf
from .media import MediaError, compatible_preview
from .hardening import SecurityMiddleware, require_admin

logger = logging.getLogger(__name__)

app = FastAPI(title="TruthLens", version="0.3.0")
app.add_middleware(SecurityMiddleware)
registry.seed()   # demo originals on first start
STATIC = os.path.join(os.path.dirname(__file__), "static")
SAMPLES = os.path.join(ROOT, "samples", "demo")
MAX_UPLOAD = int(os.environ.get("TRUTHLENS_MAX_UPLOAD_MB", "200")) * 1024 * 1024
DEMOS = {
    "edited": {"title": "Demo: edited copy of a registered photo", "files": ["rally_edited.jpg"], "caption": False},
    "original": {"title": "Demo: verified original", "files": ["rally_original.jpg"], "caption": False},
    "synthetic": {"title": "Demo: unregistered synthetic-looking portrait", "files": ["ai_portrait.png"], "caption": False},
    "full": {"title": "Demo: viral rally post (photo + clip + caption)", "files": ["rally_edited.jpg", "viral_clip.mp4", "ai_portrait.png"], "caption": True},
}
DEMO_FILES = sorted({f for d in DEMOS.values() for f in d["files"]})


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.exception_handler(Exception)
async def unhandled(request: Request, ex: Exception):
    # Log the traceback server-side; never send internals to the browser.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.get("/api/health")
def health():
    """Liveness/readiness for the hosting platform: database reachable; Sync ID reported, not required."""
    import httpx
    db.verify_chain()
    try:
        r = httpx.get(os.environ.get("SYNCID_URL", "http://127.0.0.1:8001").rstrip("/") + "/health", timeout=2.0, trust_env=False)
        syncid = "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except httpx.HTTPError:
        syncid = "unavailable"
    return {"status": "ok", "service": "truthlens", "syncid": syncid}


@app.get("/api/status")
def status():
    return {"ai_enabled": llm.available(), "model": llm.MODEL if llm.available() else None, "c2pa": HAVE_C2PA,
            "demos": {k: v["title"] for k, v in DEMOS.items()},
            "demo_available": all(os.path.exists(os.path.join(SAMPLES, f)) for f in DEMO_FILES)}


@app.post("/api/analyze")
async def analyze(files: list[UploadFile] = File(default=[]), caption: str = Form(""), title: str = Form(""),
                  reference_image: UploadFile | None = File(default=None)):
    data = []
    total = 0
    reference_data = None
    uploads = list(files) + ([reference_image] if reference_image else [])
    try:
        for f in uploads:
            chunks = []
            while chunk := await f.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, f"Upload too large for the prototype ({MAX_UPLOAD // (1024 * 1024)} MB limit)")
                chunks.append(chunk)
            b = b"".join(chunks)
            if b:
                if f is reference_image:
                    reference_data = (f.filename, b)
                else:
                    data.append((f.filename, b))
    finally:
        for f in uploads:
            await f.close()
    if not data and not caption.strip():
        raise HTTPException(400, "Add at least one file or some text")
    try:
        return JSONResponse(await run_in_threadpool(run_case, data, caption, title, reference_image=reference_data))
    except ValidationError as ex:
        logger.warning("Upload validation failed: %s", ex)
        raise HTTPException(ex.status_code, f"Upload rejected: {ex}")


@app.post("/api/demo")
def demo(scenario: str = "full"):
    d = DEMOS.get(scenario)
    if not d:
        raise HTTPException(404, "Unknown demo")
    files = []
    for n in d["files"]:
        with open(os.path.join(SAMPLES, n), "rb") as f:
            files.append((n, f.read()))
    caption = ""
    if d["caption"]:
        with open(os.path.join(SAMPLES, "caption.txt"), encoding="utf-8") as f:
            caption = f.read()
    return JSONResponse(run_case(files, caption, d["title"]))


# ---------------------------------------------------------------- registry of originals

@app.get("/api/registry")
def registry_list():
    return [{k: v for k, v in e.items() if k != "file"} for e in registry.load()]


@app.post("/api/registry")
async def registry_add(files: list[UploadFile] = File(...), source: str = Form(""), note: str = Form("")):
    from .validation import validate_file
    added = []
    if len(files) > 12:
        raise HTTPException(400, "At most 12 files per registration")
    for f in files:
        b = await f.read(MAX_UPLOAD + 1)
        await f.close()
        if len(b) > MAX_UPLOAD:
            raise HTTPException(413, f"Registration too large ({MAX_UPLOAD // (1024 * 1024)} MB limit)")
        if b:
            try:
                validate_file(f.filename, b)
            except ValidationError as ex:
                raise HTTPException(ex.status_code, f"Registration rejected: {ex}")
            e = registry.add(f.filename, b, source, note)
            added.append({k: v for k, v in e.items() if k != "file"})
    return added


@app.get("/api/registry/{rid}/file")
def registry_file(rid: str):
    e = next((e for e in registry.load() if e["id"] == rid), None)
    if not e:
        raise HTTPException(404)
    path = registry.path_of(e)
    with open(path, "rb") as f:
        head = f.read(64)
    from .validation import sniff
    kind = sniff(head)[0]
    # Content-derived type only, so an uploaded HTML/SVG-like text file can never render as a page.
    mime = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
            "tiff": "image/tiff"}.get(kind, "application/octet-stream")
    return FileResponse(path, media_type=mime)


@app.post("/api/registry/reset", dependencies=[Depends(require_admin)])
def registry_reset():
    registry.reset_to_seed()
    return registry_list()


@app.get("/api/custody/verify")
def custody_verify():
    """Re-check the whole hash-chained custody log."""
    return db.verify_chain()


@app.get("/api/cases")
def cases():
    return list_cases()


@app.get("/api/cases/{cid}")
def case(cid: str):
    r = load_case(cid)
    if not r:
        raise HTTPException(404, "Case not found")
    return r


def _safe_file(cid, sub, name):
    r = load_case(cid)
    if not r:
        raise HTTPException(404)
    base = os.path.realpath(os.path.join(case_dir(cid), sub))
    p = os.path.realpath(os.path.join(base, name))
    if not p.startswith(base + os.sep) or not os.path.isfile(p):
        raise HTTPException(404)
    return p


@app.get("/api/cases/{cid}/artifacts/{name}")
def artifact(cid: str, name: str):
    return FileResponse(_safe_file(cid, "artifacts", name))


def _safe_fallback_mime(guessed):
    # The filename-guessed type is only trusted for media; anything else (e.g. "notes.html") is served as plain text.
    if guessed and guessed.split("/")[0] in ("image", "video", "audio") and "svg" not in guessed:
        return guessed
    return "text/plain; charset=utf-8"


@app.get("/api/cases/{cid}/evidence/{eid}")
def evidence(cid: str, eid: str):
    r = load_case(cid)
    if not r:
        raise HTTPException(404)
    e = next((e for e in r["evidence"] if e["id"] == eid), None)
    if not e:
        raise HTTPException(404)
    sub, name = os.path.split(e["stored_as"])
    detected_mime = {"jpeg": "image/jpeg", "png": "image/png", "mp4/mov": "video/mp4", "avi": "video/x-msvideo",
                     "mkv/webm": "video/webm", "wav": "audio/wav", "mp3": "audio/mpeg",
                     "text": "text/plain; charset=utf-8"}.get(e.get("detected_type"))
    return FileResponse(_safe_file(cid, sub, name), filename=e["filename"].replace(" (audio track)", ".wav"),
                        media_type=detected_mime or _safe_fallback_mime(e.get("mime")), content_disposition_type="inline")


@app.get("/api/cases/{cid}/preview/{eid}")
def video_preview(cid: str, eid: str):
    r = load_case(cid)
    e = next((e for e in (r or {}).get("evidence", []) if e["id"] == eid and e["modality"] == "video"), None)
    if not e:
        raise HTTPException(404, "Video evidence not found")
    sub, name = os.path.split(e["stored_as"])
    original = _safe_file(cid, sub, name)
    output = os.path.join(case_dir(cid), "artifacts", f"{e['uuid']}_preview.mp4")
    existed = os.path.isfile(output)
    try:
        compatible_preview(original, output)
    except MediaError as ex:
        logger.warning("Compatible preview failed: %s", ex)
        raise HTTPException(ex.status_code, str(ex))
    if not existed:
        from .pipeline import sha256
        db.log_event("evidence.preview", "Created browser-compatible derivative; original preserved", r["investigation_uuid"], eid, sha256(output))
    return FileResponse(output, media_type="video/mp4", content_disposition_type="inline")


@app.get("/api/cases/{cid}/report.pdf")
def pdf(cid: str, inline: bool = False):
    r = load_case(cid)
    if not r:
        raise HTTPException(404)
    out = os.path.join(case_dir(cid), "report.pdf")
    build_pdf(r, case_dir(cid), out)
    return FileResponse(out, media_type="application/pdf", filename=f"truthlens_{cid}.pdf",
                        content_disposition_type="inline" if inline else "attachment")


@app.get("/api/cases/{cid}/report.json")
def report_json(cid: str):
    r = load_case(cid)
    if not r:
        raise HTTPException(404)
    return JSONResponse(r, headers={"Content-Disposition": f'attachment; filename="truthlens_{cid}.json"'})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _syncid_upload(action, file, sync_id=""):
    from .syncid_client import request_identity
    # Explicit upload only; never publishes existing case evidence automatically.
    try:
        data = file.file.read(25 * 1024 * 1024 + 1)
    finally:
        file.file.close()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Sync ID prototype limit: 25 MiB")
    if not data:
        raise HTTPException(400, "File is empty")
    return request_identity(action, data, file.filename or "content", sync_id)


@app.post("/api/syncid/register")
def syncid_register(file: UploadFile = File(...)):
    return _syncid_upload("register", file)


@app.post("/api/syncid/verify")
def syncid_verify(file: UploadFile = File(...), sync_id: str = Form(..., min_length=36, max_length=36)):
    return _syncid_upload("verify", file, sync_id)
