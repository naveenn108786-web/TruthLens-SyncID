"""End-to-end regression tests on the SYNTHETIC demo set (samples/make_samples.py).
They check behaviour and rules, not accuracy: the demo files were generated to contain these signals."""
import pytest
from fastapi.testclient import TestClient

from conftest import demo_bytes
from truthlens import db
from truthlens.app import app

client = TestClient(app)
NEUTRAL_CATS = {"registry", "c2pa", "metadata"}


def _demo(name):
    r = client.post(f"/api/demo?scenario={name}")
    assert r.status_code == 200, r.text
    return r.json()


def _findings(rep):
    return [f for e in rep["evidence"] for f in e["findings"]]


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by)) > 0


def test_registered_original_is_exact_match_and_low():
    rep = _demo("original")
    assert rep["overall"]["band"] == "low"
    assert any(f["category"] == "registry-match" for f in _findings(rep))


def test_edited_copy_localises_the_swapped_face():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples"))
    from make_samples import render_scene
    _, face = render_scene(1280, 720)            # ground truth box of the face that was replaced
    rep = _demo("edited")
    mod = [f for f in _findings(rep) if f["category"] == "modified-copy"]
    assert mod and _overlap(mod[0]["location"]["bbox"], face)
    assert any(f["category"] == "camera-consistency" and f["score"] > 0 and _overlap(f["location"]["bbox"], face) for f in _findings(rep))


def test_missing_registry_metadata_c2pa_are_never_indicators():
    rep = _demo("synthetic")
    fs = _findings(rep)
    neutral = [f for f in fs if f["category"] in NEUTRAL_CATS and f["indicator"] in
               ("Not found in the registry", "No content credentials", "No camera metadata")]
    assert len(neutral) == 3 and all(f["score"] == 0 and f["strength"] == "info" for f in neutral)
    assert "Not found in the registry" not in rep["summary"] and "No content credentials" not in rep["summary"]
    assert any(f.get("experimental") for f in fs)
    assert all(f["score"] <= 0.35 for f in fs if f.get("experimental"))


def test_full_case_covers_all_four_media_types_and_correlates():
    rep = _demo("full")
    mods = {e["modality"] for e in rep["evidence"]}
    assert {"image", "video", "audio", "text"} <= mods
    titles = " ".join(c["title"] for c in rep["correlations"])
    assert "aligns with video cut" in titles and "Claimed date" in titles
    assert rep["cannot_establish"] and rep["investigation"][-1]["investigation"] == "Final assessment"


def test_custody_chain_and_report_integrity():
    rep = _demo("original")
    assert rep["custody_verification"]["ok"]
    assert rep["report_integrity"] is True
    events = [l["step"] for l in rep["log"]]
    for needed in ("investigation.create", "evidence.ingest", "analysis.run", "report.generate"):
        assert needed in events
    assert rep["investigation_uuid"] and all(e.get("uuid") for e in rep["evidence"])


def test_rejected_upload_stores_nothing():
    before = len(db.list_investigations())
    r = client.post("/api/analyze", files=[("files", ("x.jpg", b"\x00garbage" * 20, "image/jpeg"))])
    assert r.status_code == 415
    assert len(db.list_investigations()) == before


def test_artifact_path_traversal_blocked():
    rep = _demo("original")
    r = client.get(f"/api/cases/{rep['case_id']}/artifacts/..%2Freport.json")
    assert r.status_code == 404
    assert client.get("/api/cases/..%2F..%2Fetc/report.pdf").status_code == 404


def test_reencoded_copy_of_registered_original_runs_without_errors():
    # Regression: a re-encoded (not edited) copy crashed the sensor-noise step (found with real files).
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(demo_bytes("rally_original.jpg"))).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    r = client.post("/api/analyze", files=[("files", ("reupload.jpg", buf.getvalue(), "image/jpeg"))])
    assert r.status_code == 200, r.text
    rep = r.json()
    assert not [l for l in rep["log"] if l["step"] == "analysis.error"]
    reg = rep["evidence"][0]["provenance"]["registry"]
    assert not reg["exact"] and reg.get("similar")      # recognised as a near-copy of R001, not an exact match
    assert client.get(f"/api/cases/{rep['case_id']}/report.pdf").status_code == 200


def _upload(name, data):
    r = client.post("/api/analyze", files=[("files", (name, data, "application/octet-stream"))])
    assert r.status_code == 200, r.text
    return r.json()


def _core(rep):
    e = rep["evidence"][0]
    return (rep["overall"]["score"], e["sha256"], sorted((f["indicator"], f["score"]) for f in e["findings"] if f["category"] != "history"))


def test_results_depend_on_content_not_filename():
    data = demo_bytes("rally_edited.jpg")
    a, b = _upload("rally_edited.jpg", data), _upload("holiday_photo_final.jpg", data)
    assert _core(a) == _core(b)


def test_one_pixel_change_changes_hash_and_breaks_exact_match():
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(demo_bytes("rally_original.jpg"))).convert("RGB")
    im.putpixel((5, 5), (0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    rep = _upload("rally_original.png", buf.getvalue())
    orig = _demo("original")
    assert rep["evidence"][0]["sha256"] != orig["evidence"][0]["sha256"]
    assert not rep["evidence"][0]["provenance"]["registry"]["exact"]


def test_pdf_report_matches_its_case():
    import subprocess, tempfile, shutil
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    for name in ("edited", "full"):
        rep = _demo(name)
        pdf = client.get(f"/api/cases/{rep['case_id']}/report.pdf")
        assert pdf.status_code == 200
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf.content)
        text = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True).stdout
        assert rep["case_id"] in text and rep["investigation_uuid"] in text
        assert f"{rep['overall']['score']:.2f}" in text
        for e in rep["evidence"]:
            assert e["sha256"] in text.replace("\n", "")
            for fd in e["findings"]:
                assert fd["id"] in text
        assert "Method and evidence levels" in text and "Strongest signal per engine" in text
        if name == "full":
            assert "Frames cited by findings" in text and "Waveform of the analysed signal" in text


def test_video_frame_records_are_real_and_linked():
    rep = _demo("full")
    v = next(e for e in rep["evidence"] if e["modality"] == "video")
    frames = v["metadata"]["frames"]
    assert frames and all(0 <= f["t"] <= v["metadata"]["duration_s"] for f in frames)
    ids = {f["id"] for f in v["findings"]}
    assert any(f["findings"] for f in frames) and all(set(f["findings"]) <= ids for f in frames)
    r = client.get(f"/api/cases/{rep['case_id']}/artifacts/{frames[0]['thumb']}")
    assert r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff"


def test_waveform_and_heat_layer_come_from_the_analysis():
    rep = _demo("full")
    a = next(e for e in rep["evidence"] if e["modality"] == "audio")
    wf = a["metadata"]["waveform"]
    assert wf["bins"] == len(wf["min"]) == len(wf["max"]) > 100
    assert all(lo <= hi for lo, hi in zip(wf["min"], wf["max"])) and max(wf["max"]) > 0.01
    img = next(e for e in rep["evidence"] if e["modality"] == "image" and e["artifacts"].get(e["id"] + "_heat"))
    name = img["artifacts"][img["id"] + "_heat"]
    r = client.get(f"/api/cases/{rep['case_id']}/artifacts/{name}")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_dashboard_counts_come_from_the_stored_report():
    rep = _demo("full")
    row = next(c for c in client.get("/api/cases").json() if c["case_id"] == rep["case_id"])
    F = _findings(rep)
    assert row["evidence_count"] == len(rep["evidence"]) and row["findings"] == len(F)
    assert row["indicators"] == sum(1 for f in F if f["score"] >= 0.4)
    assert row["correlations"] == len(rep["correlations"]) and row["label"] == rep["overall"]["label"]
    assert set(row["modalities"]) == {e["modality"] for e in rep["evidence"]}


def test_same_date_claim_in_two_texts_counts_once():
    # Regression (found with real files): the same date in the caption and in an uploaded .txt produced duplicate
    # "Claimed date" relations, each strengthening the overall indicator.
    claim = "Unedited live footage filmed yesterday."
    one = client.post("/api/analyze", files=[("files", ("photo.jpg", demo_bytes("rally_original.jpg"), "image/jpeg"))],
                      data={"caption": claim}).json()
    two = client.post("/api/analyze", files=[("files", ("photo.jpg", demo_bytes("rally_original.jpg"), "image/jpeg")),
                                             ("files", ("post.txt", claim.encode(), "text/plain"))], data={"caption": claim}).json()
    dates = lambda r: [c["title"] for c in r["correlations"] if c["title"].startswith("Claimed date")]
    assert len(dates(two)) == len(set(dates(two))) == len(dates(one))
    assert two["overall"]["score"] <= one["overall"]["score"] + 0.05
