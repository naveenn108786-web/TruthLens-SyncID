"""C2PA content-credential verification (uses the official c2pa-python SDK when installed).

C2PA is a signed manifest embedded in the file. If present we report:
- whether the signature and content hash are valid (file unchanged since signing)
- whether the signer's certificate is on a trust list
- who/what signed it and the recorded actions (captured, edited, AI-generated...)
Most images on the internet carry no credentials at all, so "none" is NEUTRAL, never a red flag.
"""
import json

try:
    import c2pa
    HAVE_C2PA = True
except Exception:   # library not installed: fall back to marker detection only
    HAVE_C2PA = False


def _has_marker(path):
    try:
        with open(path, "rb") as f:
            data = f.read(4 * 1024 * 1024)
        return b"c2pa" in data and b"jumb" in data
    except Exception:
        return False


def check_c2pa(path):
    """Return {status, signer, generator, actions, ai_generated, failures, detail}.

    status: none | valid | valid_untrusted | invalid | present_unverified | error
    """
    if not HAVE_C2PA:
        if _has_marker(path):
            return {"status": "present_unverified", "detail": "A C2PA manifest is present but the c2pa library is not installed, so it was not verified."}
        return {"status": "none", "detail": "No content credentials found (c2pa library not installed; marker scan only)."}
    try:
        with c2pa.Reader(path) as r:
            manifest = json.loads(r.json())
            results = r.get_validation_results() or {}
            state = r.get_validation_state()
    except Exception as ex:
        msg = str(ex)
        if "ManifestNotFound" in msg or "no JUMBF" in msg.lower() or "not found" in msg.lower():
            return {"status": "none", "detail": "No content credentials (C2PA manifest) in this file."}
        if _has_marker(path):
            return {"status": "invalid", "detail": f"A C2PA manifest is present but could not be read: {msg[:160]}"}
        return {"status": "none", "detail": "No content credentials (C2PA manifest) in this file."}

    active = manifest.get("manifests", {}).get(manifest.get("active_manifest"), {})
    sig = active.get("signature_info", {}) or {}
    gen = active.get("claim_generator_info") or [{}]
    actions = []
    ai = False
    for a in active.get("assertions", []):
        if a.get("label", "").startswith("c2pa.actions"):
            for act in a.get("data", {}).get("actions", []):
                actions.append(act.get("action"))
                if "trainedAlgorithmicMedia" in str(act.get("digitalSourceType", "")) or "compositeWithTrainedAlgorithmicMedia" in str(act.get("digitalSourceType", "")):
                    ai = True
    am = (results or {}).get("activeManifest", {}) or {}
    failures = [f.get("code") for f in am.get("failure", [])]
    untrusted_only = failures and all(c == "signingCredential.untrusted" for c in failures)
    if not failures:
        status = "valid"
    elif untrusted_only:
        status = "valid_untrusted"
    else:
        status = "invalid"
    return {"status": status, "state": str(state), "signer": sig.get("common_name") or sig.get("issuer"),
            "issuer": sig.get("issuer"), "signed_at": sig.get("time"),
            "generator": (gen[0] or {}).get("name") if isinstance(gen, list) else str(gen),
            "actions": actions, "ai_generated": ai, "failures": failures,
            "title": active.get("title")}
