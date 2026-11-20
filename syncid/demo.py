"""DemoAI is a simulated producer, not an actual company integration.

Run against a live service: python -m syncid.demo --truthlens-url http://127.0.0.1:8000
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from .crypto import decode, verify_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--truthlens-url", default="")
    parser.add_argument("--output", default="syncid-demo-output")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (640, 360), "#102738")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 600, 320), outline="#32d9b0", width=5)
    draw.text((70, 130), "DemoAI - SIMULATED PRODUCER", fill="white")
    draw.text((70, 170), "Sync ID signed content registration demo", fill="#32d9b0")
    original = io.BytesIO()
    image.save(original, "PNG")
    original = original.getvalue()
    draw.rectangle((460, 220, 580, 290), fill="#e76f51")
    modified = io.BytesIO()
    image.save(modified, "PNG")
    modified = modified.getvalue()
    (output / "original.png").write_bytes(original)
    (output / "modified.png").write_bytes(modified)
    headers = {}
    if os.environ.get("SYNCID_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["SYNCID_API_TOKEN"]
    with httpx.Client(timeout=20, trust_env=False) as client:
        def post(path, data, content):
            r = client.post(args.url.rstrip("/") + path, data=data,
                            files={"file": ("demo.png", content, "image/png")}, headers=headers)
            r.raise_for_status()
            return r.json()
        registration = post("/v1/register", {"producer_label": "DemoAI (SIMULATED integration)", "producer_simulated": "true"}, original)
        sync_id = registration["sync_id"]
        duplicate = post("/v1/register", {}, original)
        good = post("/v1/verify", {"sync_id": sync_id}, original)
        bad = post("/v1/verify", {"sync_id": sync_id}, modified)
        key_response = client.get(args.url.rstrip("/") + "/v1/public-key")
        key_response.raise_for_status()
        public = decode(key_response.json()["public_key"])
        assert duplicate["sync_id"] == sync_id and duplicate["created"] is False
        assert registration["record"]["payload"]["sha256"] == hashlib.sha256(original).hexdigest()
        assert good["content_verified"] and good["hash_match"]
        assert not bad["content_verified"] and not bad["hash_match"]
        assert bad["signature_valid"] and verify_record(registration["record"], public)
        results = {"producer": "DemoAI — SIMULATED, not an actual external company", "sync_id": sync_id,
                   "duplicate_reuses_id": True, "original": good, "modified": bad,
                   "local_signature_verification": True, "origin_status": "not_established"}
        if args.truthlens_url:
            results["truthlens"] = {}
            for name, content in (("original", original), ("modified", modified)):
                r = client.post(args.truthlens_url.rstrip("/") + "/api/syncid/verify", data={"sync_id": sync_id},
                                files={"file": (name + ".png", content, "image/png")})
                r.raise_for_status()
                result = r.json()
                assert result["available"] and result["signature_valid"]
                assert result["hash_match"] is (name == "original")
                results["truthlens"][name] = result
    (output / "identity.json").write_text(json.dumps(registration["record"], indent=2), encoding="utf-8")
    (output / "public-key.json").write_text(json.dumps(key_response.json(), indent=2), encoding="utf-8")
    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("DemoAI: SIMULATED integration")
    print("Sync ID:", sync_id)
    print("Duplicate: same identity")
    print("Original: SHA-256 MATCH; signature VALID")
    print("Modified: SHA-256 MISMATCH; original registration signature STILL VALID")
    if args.truthlens_url:
        print("TruthLens HTTP integration: original and modified checks PASSED")
    print("Origin: NOT ESTABLISHED. Artifacts:", output.resolve())


if __name__ == "__main__":
    main()
