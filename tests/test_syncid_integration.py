import hashlib

import httpx
from fastapi.testclient import TestClient

from truthlens import syncid_client
from truthlens.app import app
from syncid.crypto import encode
from syncid.engine import IdentityEngine


def service_client(tmp_path, monkeypatch):
    engine = IdentityEngine(tmp_path / "sync.db", tmp_path / "key.pem")
    content = b"registered content"
    record, _ = engine.register(hashlib.sha256(content).hexdigest(), len(content), "application/octet-stream")
    def handle(request):
        if request.url.path.endswith("/public-key"):
            return httpx.Response(200, json={"public_key": encode(engine.public)})
        return httpx.Response(200, json={"record": record})
    monkeypatch.setenv("SYNCID_PUBLIC_KEY", encode(engine.public))
    return httpx.Client(transport=httpx.MockTransport(handle)), record, content


def test_adapter_verifies_locally_and_distinguishes_modified(tmp_path, monkeypatch):
    client, record, content = service_client(tmp_path, monkeypatch)
    with client:
        sync_id = record["payload"]["sync_id"]
        good = syncid_client.request_identity("verify", content, "x", sync_id, client)
        bad = syncid_client.request_identity("verify", content + b"edit", "x", sync_id, client)
    assert good["content_verified"] and good["issuer_trust"] == "key_pinned"
    assert bad["signature_valid"] and not bad["hash_match"] and not bad["content_verified"]
    assert good["origin_status"] == bad["origin_status"] == "not_established"


def test_adapter_rejects_unexpected_identity(tmp_path, monkeypatch):
    client, record, content = service_client(tmp_path, monkeypatch)
    with client:
        result = syncid_client.request_identity("verify", content, "x", "another-id", client)
    assert not result["signature_valid"] and not result["content_verified"]


def test_outage_is_explicit_and_truthlens_still_responds(monkeypatch):
    def unavailable(request):
        raise httpx.ConnectError("offline", request=request)
    real_request = syncid_client.request_identity
    with httpx.Client(transport=httpx.MockTransport(unavailable)) as remote:
        monkeypatch.setattr(syncid_client, "request_identity", lambda *args: real_request(*args, client=remote))
        client = TestClient(app)
        response = client.post("/api/syncid/verify", data={"sync_id": "019a0000-0000-7000-8000-000000000001"}, files={"file": ("x", b"content")})
        assert response.status_code == 200
        result = response.json()
        assert not result["available"] and result["registration_status"] == "unavailable"
        assert result["hash_match"] is None
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/registry").status_code == 200
        analysis = client.post("/api/analyze", data={"caption": "A plain text observation."})
        assert analysis.status_code == 200


def test_ui_and_explicit_routes_present(monkeypatch):
    client = TestClient(app)
    assert "syncidPanel" in client.get("/").text
    assert client.get("/static/syncid.js").status_code == 200
    monkeypatch.setattr(syncid_client, "request_identity", lambda *args: {"available": True, "sync_id": "demo"})
    assert client.post("/api/syncid/register", files={"file": ("x", b"test")}).json()["sync_id"] == "demo"
    assert client.post("/api/syncid/register", files={"file": ("x", b"")}).status_code == 400
