"""SQLite storage: investigations, evidence, registry of originals, and the custody log.

The custody log is TAMPER-EVIDENT: every entry stores the SHA-256 of the previous entry, and its
own hash covers its content plus that previous hash. Changing, deleting or re-ordering any past
entry breaks the chain from that point on, which verify_chain() detects. (This makes tampering
detectable, not impossible: someone with write access could rebuild the whole chain, so for real
use the latest hash would also be anchored outside the database, e.g. printed in each report.)
"""
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("TRUTHLENS_DB", os.path.join(ROOT, "data", "truthlens.db"))
GENESIS = "0" * 64
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,            -- UUID
    case_ref TEXT UNIQUE NOT NULL,  -- human-readable id, also the folder name
    title TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL,
    assessment TEXT, indicator REAL, report_path TEXT, report_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,            -- UUID
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    ref TEXT NOT NULL,              -- E1, E2, E3a ...
    filename TEXT, modality TEXT, detected_type TEXT, sha256 TEXT NOT NULL, size_bytes INTEGER,
    stored_path TEXT, received_at TEXT, derived_from TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS evidence_sha ON evidence(sha256);
CREATE TABLE IF NOT EXISTS registry (
    id TEXT PRIMARY KEY,            -- UUID (digital media identity)
    ref TEXT UNIQUE NOT NULL,       -- R001 ...
    filename TEXT, sha256 TEXT UNIQUE NOT NULL, phash TEXT, size_bytes INTEGER, width INTEGER, height INTEGER,
    capture_date TEXT, device TEXT, software TEXT, c2pa_status TEXT,
    source TEXT, note TEXT, registered_at TEXT NOT NULL, stored_path TEXT NOT NULL, metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS custody_log (
    seq INTEGER PRIMARY KEY,
    ts TEXT NOT NULL, investigation_id TEXT, actor TEXT, event TEXT NOT NULL, detail TEXT, ref TEXT, data_sha256 TEXT,
    prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL
);
"""


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def new_id():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- custody log (hash chain)

def _entry_hash(prev_hash, seq, ts, investigation_id, actor, event, detail, ref, data_sha256):
    payload = json.dumps([seq, ts, investigation_id, actor, event, detail, ref, data_sha256], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def log_event(event, detail, investigation_id=None, ref=None, data_sha256=None, actor="truthlens"):
    """Append one custody entry, chained to the previous one. Returns the entry dict."""
    with _write_lock, connect() as con:
        last = con.execute("SELECT seq, entry_hash FROM custody_log ORDER BY seq DESC LIMIT 1").fetchone()
        seq = (last["seq"] + 1) if last else 1
        prev = last["entry_hash"] if last else GENESIS
        ts = now()
        h = _entry_hash(prev, seq, ts, investigation_id, actor, event, detail, ref, data_sha256)
        con.execute("INSERT INTO custody_log VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (seq, ts, investigation_id, actor, event, detail, ref, data_sha256, prev, h))
    return {"seq": seq, "time": ts, "event": event, "detail": detail, "ref": ref, "data_sha256": data_sha256,
            "prev_hash": prev, "entry_hash": h}


def verify_chain():
    """Recompute every entry hash in order. Returns {ok, entries, broken_at, head}."""
    with connect() as con:
        rows = con.execute("SELECT * FROM custody_log ORDER BY seq").fetchall()
    prev = GENESIS
    expected_seq = 1
    for r in rows:
        if r["seq"] != expected_seq or r["prev_hash"] != prev:
            return {"ok": False, "entries": len(rows), "broken_at": r["seq"], "reason": "entry missing or re-ordered"}
        h = _entry_hash(prev, r["seq"], r["ts"], r["investigation_id"], r["actor"], r["event"], r["detail"], r["ref"], r["data_sha256"])
        if h != r["entry_hash"]:
            return {"ok": False, "entries": len(rows), "broken_at": r["seq"], "reason": "entry content changed"}
        prev = h
        expected_seq += 1
    return {"ok": True, "entries": len(rows), "broken_at": None, "head": prev}


def custody_for(investigation_id):
    with connect() as con:
        rows = con.execute("SELECT * FROM custody_log WHERE investigation_id=? ORDER BY seq", (investigation_id,)).fetchall()
    return [{"seq": r["seq"], "time": r["ts"], "step": r["event"], "detail": r["detail"], "ref": r["ref"],
             "data_sha256": r["data_sha256"], "prev_hash": r["prev_hash"], "entry_hash": r["entry_hash"]} for r in rows]


# ---------------------------------------------------------------- investigations / evidence

def create_investigation(case_ref, title):
    iid = new_id()
    with _write_lock, connect() as con:
        con.execute("INSERT INTO investigations (id, case_ref, title, created_at, status) VALUES (?,?,?,?,?)",
                    (iid, case_ref, title, now(), "open"))
    return iid


def finish_investigation(iid, assessment, indicator, report_path, report_sha256):
    with _write_lock, connect() as con:
        con.execute("UPDATE investigations SET status='reported', assessment=?, indicator=?, report_path=?, report_sha256=? WHERE id=?",
                    (assessment, indicator, report_path, report_sha256, iid))


def add_evidence(iid, item):
    eid = new_id()
    with _write_lock, connect() as con:
        con.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, iid, item["id"], item["filename"], item["modality"], item.get("detected_type"), item["sha256"],
                     item["size_bytes"], item.get("stored_as"), item.get("received_at"), item.get("derived_from"), item.get("note")))
    return eid


def list_investigations():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM investigations ORDER BY created_at DESC").fetchall()]


def investigation_by_ref(case_ref):
    with connect() as con:
        r = con.execute("SELECT * FROM investigations WHERE case_ref=?", (case_ref,)).fetchone()
    return dict(r) if r else None


def evidence_by_sha(sha, exclude_investigation=None):
    with connect() as con:
        rows = con.execute("""SELECT e.*, i.case_ref, i.title, i.created_at AS case_created, i.indicator
                              FROM evidence e JOIN investigations i ON i.id = e.investigation_id
                              WHERE e.sha256=? AND e.investigation_id IS NOT ? ORDER BY i.created_at""",
                           (sha, exclude_investigation)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- registry

def registry_rows():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM registry ORDER BY ref").fetchall()]


def registry_insert(row):
    with _write_lock, connect() as con:
        n = con.execute("SELECT COUNT(*) FROM registry").fetchone()[0]
        row = dict(row, id=row.get("id") or new_id(), ref=f"R{n + 1:03d}")
        cols = ",".join(row)
        con.execute(f"INSERT INTO registry ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    return row


def registry_clear():
    with _write_lock, connect() as con:
        con.execute("DELETE FROM registry")
