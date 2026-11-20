import copy
import hashlib
import io
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from syncid.app import create_app
from syncid.crypto import decode, verify_record
from syncid.engine import IdentityEngine


@pytest.fixture
def engine(tmp_path):
    return IdentityEngine(tmp_path / "identity.db", tmp_path / "key.pem", "test-registry")


@pytest.fixture
def client(engine):
    with TestClient(create_app(engine)) as client:
        yield client


@pytest.fixture
def content():
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(out, "PNG")
    return out.getvalue()


def register(client, content, **fields):
    return client.post("/v1/register", files={"file": ("fake.jpg", content, "text/plain")}, data=fields)


def verify(client, sync_id, content):
    return client.post("/v1/verify", data={"sync_id": sync_id}, files={"file": ("x.png", content)})


def test_registration_sha_uuid_media_and_signature(client, engine, content):
    response = register(client, content, producer_label="DemoAI (simulated)", producer_simulated="true")
    assert response.status_code == 201
    body = response.json()
    payload = body["record"]["payload"]
    assert uuid.UUID(body["sync_id"]).version == 7
    assert payload["sha256"] == hashlib.sha256(content).hexdigest()
    assert payload["size_bytes"] == len(content)
    assert payload["media_type"] == "image/png"  # not client MIME or filename
    assert payload["registered_at"].endswith("Z")
    assert payload["producer_simulated"] is True
    assert body["origin_status"] == "not_established"
    assert verify_record(body["record"], engine.public)
    assert decode(client.get("/v1/public-key").json()["public_key"]) == engine.public
    assert client.get("/v1/identity/" + body["sync_id"]).json()["record"] == body["record"]


def test_duplicate_preserves_signed_record(client, content):
    a = register(client, content, producer_label="First").json()
    response = register(client, content, producer_label="Do not overwrite")
    b = response.json()
    assert response.status_code == 200 and b["created"] is False
    assert a["sync_id"] == b["sync_id"] and a["record"] == b["record"]


def test_original_and_modified_file(client, content):
    original = register(client, content).json()
    good = verify(client, original["sync_id"], content).json()
    image = Image.open(io.BytesIO(content))
    image.putpixel((0, 0), (255, 0, 0))
    out = io.BytesIO()
    image.save(out, "PNG")
    bad = verify(client, original["sync_id"], out.getvalue()).json()
    assert good["hash_match"] and good["content_verified"]
    assert not bad["hash_match"] and not bad["content_verified"]
    assert bad["verification_status"] == "rejected"
    assert bad["signature_valid"] and bad["registration_status"] == "registered"
    assert bad["record"] == original["record"]


def test_signature_tampering_and_wrong_key(client, engine, content, tmp_path):
    record = register(client, content).json()["record"]
    altered = copy.deepcopy(record)
    altered["payload"]["producer_label"] = "attacker"
    assert not verify_record(altered, engine.public)
    altered = copy.deepcopy(record)
    altered["signature"] = "invalid!"
    assert not verify_record(altered, engine.public)
    other = IdentityEngine(tmp_path / "other.db", tmp_path / "other.pem")
    assert not verify_record(record, other.public)
    reordered = {k: record[k] for k in reversed(record)}
    assert verify_record(reordered, engine.public)


def test_persists_across_engine_restart(engine, content):
    record, _ = engine.register(hashlib.sha256(content).hexdigest(), len(content), "image/png")
    restarted = IdentityEngine(engine.db_path, engine.key_path)
    assert restarted.get(record["payload"]["sync_id"]) == record
    assert restarted.public == engine.public
    assert verify_record(record, restarted.public)


def test_concurrent_duplicate_registration(engine, content):
    def add(_):
        return engine.register(hashlib.sha256(content).hexdigest(), len(content), "image/png")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(add, range(16)))
    assert len({r[0]["payload"]["sync_id"] for r in results}) == 1
    assert sum(created for _, created in results) == 1


def test_missing_key_does_not_silently_replace_identity(engine, content):
    engine.register(hashlib.sha256(content).hexdigest(), len(content), "image/png")
    engine.key_path.unlink()
    with pytest.raises(RuntimeError, match="Signing key missing"):
        IdentityEngine(engine.db_path, engine.key_path)


def test_empty_unknown_and_health(client):
    assert register(client, b"").status_code == 400
    assert client.get("/v1/identity/unknown").status_code == 404
    assert verify(client, "unknown", b"hello").status_code == 404
    assert client.get("/health").json()["status"] == "ok"


def test_size_limit(client, monkeypatch):
    monkeypatch.setattr("syncid.app.MAX_BYTES", 10)
    assert register(client, b"x" * 11).status_code == 413
    assert verify(client, "unknown", b"x" * 11).status_code == 413


def test_optional_registration_auth(client, monkeypatch):
    monkeypatch.setenv("SYNCID_API_TOKEN", "test-only")
    assert register(client, b"hello").status_code == 401
    response = client.post("/v1/register", files={"file": ("x", b"hello")}, headers={"Authorization": "Bearer test-only"})
    assert response.status_code == 201
    assert client.get("/health").json()["registration_auth"] == "bearer"


def test_database_record_tampering_detected(client, engine, content):
    body = register(client, content).json()
    import json
    altered = body["record"]
    altered["payload"]["sha256"] = "0" * 64
    with sqlite3.connect(engine.db_path) as con:
        con.execute("UPDATE identities SET record_json=?", (json.dumps(altered),))
    result = verify(client, body["sync_id"], content).json()
    assert not result["signature_valid"] and not result["content_verified"]
