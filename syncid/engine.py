"""Independent SQLite registration engine. Stores signed records, never media bytes."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from uuid6 import uuid7

from .crypto import encode, key_id, public_bytes, signing_bytes, verify_record

SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    sync_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
    media_type TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    key_id TEXT NOT NULL,
    record_json TEXT NOT NULL
);
PRAGMA user_version=1;
"""


class IdentityEngine:
    def __init__(self, db_path=None, key_path=None, issuer=None):
        home = Path.home() / ".syncid"
        self.db_path = Path(db_path or os.environ.get("SYNCID_DB", str(home / "registry.sqlite3")))
        self.key_path = Path(key_path or os.environ.get("SYNCID_KEY_PATH", str(home / "signing-key.pem")))
        self.issuer = issuer or os.environ.get("SYNCID_ISSUER", "syncid-local-demo")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)
            count = con.execute("SELECT COUNT(*) FROM identities").fetchone()[0]
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            if count:
                raise RuntimeError("Signing key missing for an existing registry; restore the original key.")
            key = Ed25519PrivateKey.generate()
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            # Exclusive creation prevents accidentally replacing an existing key.
            try:
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(pem)
                    f.flush()
                    os.fsync(f.fileno())
            except FileExistsError:
                pass
        self.key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        if not isinstance(self.key, Ed25519PrivateKey):
            raise RuntimeError("SYNCID_KEY_PATH must contain an Ed25519 private key")
        self.public = public_bytes(self.key.public_key())
        self.kid = key_id(self.public)
        with self.connect() as con:
            if con.execute("SELECT 1 FROM identities WHERE key_id != ? LIMIT 1", (self.kid,)).fetchone():
                raise RuntimeError("Signing key does not match this registry; restore the original key.")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            with con:
                yield con
        finally:
            con.close()

    def get(self, sync_id):
        with self.connect() as con:
            row = con.execute("SELECT record_json FROM identities WHERE sync_id=?", (sync_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def register(self, digest, size, media_type, producer_label="", producer_simulated=False):
        # Only called with a server-calculated digest and byte count, not request JSON.
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT record_json, size_bytes FROM identities WHERE sha256=?", (digest,)).fetchone()
            if row:
                if row["size_bytes"] != size:
                    raise ValueError("Digest/size inconsistency")
                return json.loads(row["record_json"]), False
            for _ in range(3):
                sync_id = str(uuid7())
                if not con.execute("SELECT 1 FROM identities WHERE sync_id=?", (sync_id,)).fetchone():
                    break
            else:
                raise RuntimeError("Unable to allocate a unique identity")
            payload = {"sync_id": sync_id, "hash_algorithm": "sha-256", "sha256": digest,
                       "size_bytes": size, "media_type": media_type,
                       "registered_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                       "producer_label": producer_label, "producer_simulated": producer_simulated}
            record = {"protected": {"type": "syncid.registration", "version": "1", "alg": "Ed25519",
                                    "kid": self.kid, "issuer": self.issuer}, "payload": payload}
            record["signature"] = encode(self.key.sign(signing_bytes(record)))
            con.execute("INSERT INTO identities VALUES (?,?,?,?,?,?,?)", (sync_id, digest, size, media_type,
                        payload["registered_at"], self.kid, json.dumps(record, ensure_ascii=False)))
        return record, True

    def describe(self, record):
        valid = verify_record(record, self.public)
        return {"sync_id": record["payload"]["sync_id"], "record": record,
                "signature_valid": valid, "signature_status": "valid" if valid else "invalid",
                "registration_status": "registered" if valid else "invalid_record",
                "issuer_trust": "not_established", "origin_status": "not_established"}
