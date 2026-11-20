"""Registry of known originals (e.g. a newsroom's or agency's verified photos), stored in SQLite.

Each registered file gets a UUID (its digital media identity) plus:
- SHA-256: proves an *exact* byte-for-byte match (identity, NOT authenticity)
- pHash (64-bit DCT perceptual hash): survives re-compression, resizing and small edits, so it
  finds visually similar or modified versions (similarity, NOT proof of ancestry)
- a metadata snapshot (capture date, device, software) and its C2PA status
The registry only knows content registered in TruthLens; it is not internet-wide origin tracking.
"""
import hashlib
import json
import os
import re
import shutil

import numpy as np
from PIL import Image
from scipy.fft import dctn

from . import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG_DIR = os.environ.get("TRUTHLENS_REGISTRY", os.path.join(ROOT, "data", "registry"))
SEED_DIR = os.path.join(ROOT, "samples", "registry")
SIMILAR_MAX_DIST = 10       # of 64 bits: <= 10 differing bits => "visually similar"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def phash(img_or_path):
    """64-bit perceptual hash: low-frequency DCT of a 32x32 greyscale thumbnail vs its median."""
    img = Image.open(img_or_path) if isinstance(img_or_path, str) else img_or_path
    g = np.asarray(img.convert("L").resize((32, 32), Image.LANCZOS), dtype=np.float64)
    low = dctn(g, norm="ortho")[:8, :8].flatten()
    bits = low[1:] > np.median(low[1:])
    bits = np.concatenate([[low[0] > 0], bits])
    return "".join("1" if b else "0" for b in bits)


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def _public(row):
    """DB row -> dict used by the rest of the app (id = human ref like R001)."""
    return {"id": row["ref"], "uuid": row["id"], "filename": row["filename"], "sha256": row["sha256"], "phash": row["phash"],
            "size_bytes": row["size_bytes"], "width": row["width"], "height": row["height"],
            "capture_date": row["capture_date"], "device": row["device"], "software": row["software"],
            "c2pa": row["c2pa_status"], "source": row["source"], "note": row["note"],
            "registered_at": row["registered_at"], "file": row["stored_path"]}


def load():
    return [_public(r) for r in db.registry_rows()]


def add(filename, data, source="", note=""):
    from .forensics.image import read_metadata
    from .forensics.c2pa_check import check_c2pa
    sha = sha256_bytes(data)
    existing = next((e for e in load() if e["sha256"] == sha), None)
    if existing:
        return existing
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[:100] or "file"
    uid = db.new_id()
    os.makedirs(os.path.join(REG_DIR, "files"), exist_ok=True)
    path = os.path.join(REG_DIR, "files", f"{uid}_{safe}")
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o444)       # registered originals are preserved read-only
    row = {"id": uid, "filename": safe, "sha256": sha, "size_bytes": len(data), "source": source, "note": note,
           "registered_at": db.now(), "stored_path": os.path.relpath(path, REG_DIR), "phash": None}
    meta = {}
    try:
        img = Image.open(path)
        meta = read_metadata(img)
        row.update({"phash": phash(img), "width": img.width, "height": img.height,
                    "capture_date": meta.get("capture_date"), "device": meta.get("device"), "software": meta.get("software")})
    except Exception:
        pass     # non-image: exact (SHA-256) matching only
    row["c2pa_status"] = check_c2pa(path).get("status")
    row["metadata_json"] = json.dumps(meta, default=str)[:20000]
    row = db.registry_insert(row)
    db.log_event("registry.register", f"Registered original {row['ref']} '{safe}' (source: {source or 'n/a'}), media UUID {uid}",
                 ref=row["ref"], data_sha256=sha)
    return _public(row)


def path_of(entry):
    return os.path.join(REG_DIR, entry["file"])


def lookup(path, sha):
    """Return {"exact": entry|None, "similar": [(entry, distance), ...], "phash": str|None}"""
    entries = load()
    exact = next((e for e in entries if e["sha256"] == sha), None)
    similar = []
    ph = None
    try:
        ph = phash(path)
        for e in entries:
            if e.get("phash") and e is not exact:
                d = hamming(ph, e["phash"])
                if d <= SIMILAR_MAX_DIST:
                    similar.append((e, d))
    except Exception:
        pass
    similar.sort(key=lambda x: x[1])
    return {"exact": exact, "similar": similar, "phash": ph}


def seed():
    """Load the demo originals on first start so the demo works out of the box."""
    if load() or not os.path.isdir(SEED_DIR):
        return
    sources = {}
    sp = os.path.join(SEED_DIR, "sources.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            sources = json.load(f)
    for n in sorted(os.listdir(SEED_DIR)):
        if n.endswith(".json"):
            continue
        with open(os.path.join(SEED_DIR, n), "rb") as f:
            s = sources.get(n, {})
            add(n, f.read(), s.get("source", "Demo registry"), s.get("note", ""))


def reset_to_seed():
    db.registry_clear()
    files = os.path.join(REG_DIR, "files")
    if os.path.isdir(files):
        for n in os.listdir(files):
            p = os.path.join(files, n)
            os.chmod(p, 0o644)
            os.remove(p)
    db.log_event("registry.reset", "Registry cleared and re-seeded with demo originals")
    seed()
