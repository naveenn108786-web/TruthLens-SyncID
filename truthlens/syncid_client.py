"""Optional HTTP integration; no Sync ID database or signing-key access."""
import hashlib
import os

import httpx

from syncid.crypto import decode, verify_record


def request_identity(action, data, filename, sync_id="", client=None):
    base = os.environ.get("SYNCID_URL", "http://127.0.0.1:8001").rstrip("/")
    fields = {"sync_id": sync_id} if action == "verify" else {"producer_label": "TruthLens user (self-reported)"}
    headers = {}
    if action == "register" and os.environ.get("SYNCID_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["SYNCID_API_TOKEN"]
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(8.0, connect=2.0), trust_env=False)
    try:
        response = client.post(base + "/v1/" + action, data=fields,
                               files={"file": (filename, data, "application/octet-stream")}, headers=headers)
        if response.status_code == 404:
            return {"available": True, "sync_id": sync_id, "registration_status": "not_found",
                    "signature_status": "not_checked", "hash_match": None, "content_verified": False,
                    "origin_status": "not_established"}
        response.raise_for_status()
        body = response.json()
        record = body["record"]
        pinned = os.environ.get("SYNCID_PUBLIC_KEY", "").strip()
        if pinned:
            public = decode(pinned)
        else:
            key_response = client.get(base + "/v1/public-key")
            key_response.raise_for_status()
            public = decode(key_response.json()["public_key"])
        valid = verify_record(record, public)
        payload = record["payload"]
        if action == "verify" and payload["sync_id"] != sync_id:
            valid = False
        calculated = hashlib.sha256(data).hexdigest()
        match = calculated == payload["sha256"] and len(data) == payload["size_bytes"]
        return {"available": True, "sync_id": payload["sync_id"], "record": record,
                "calculated_sha256": calculated, "registered_sha256": payload["sha256"],
                "hash_match": match, "signature_valid": valid, "signature_status": "valid" if valid else "invalid",
                "registration_status": "registered" if valid else "invalid_record",
                "content_verified": match and valid,
                "issuer_trust": "key_pinned" if pinned and valid else "not_established",
                "origin_status": "not_established", "created": body.get("created"),
                "verification_note": "TruthLens calculated the file hash and verified the record locally. Registration does not prove origin."}
    except httpx.RequestError:
        return {"available": False, "sync_id": sync_id, "registration_status": "unavailable",
                "signature_status": "not_checked", "hash_match": None, "content_verified": False,
                "origin_status": "not_established", "message": "Sync ID is unavailable. TruthLens forensic analysis remains available."}
    except (httpx.HTTPStatusError, ValueError, KeyError, TypeError):
        return {"available": True, "sync_id": sync_id, "registration_status": "service_error",
                "signature_status": "not_checked", "hash_match": None, "content_verified": False,
                "origin_status": "not_established", "message": "Sync ID returned an invalid response or rejected the request."}
    finally:
        if own_client:
            client.close()
