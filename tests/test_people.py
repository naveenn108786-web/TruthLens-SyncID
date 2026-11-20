"""Tests for the EXPERIMENTAL people-consistency analysis.

The group photos here are built from the bundled demo scene (its drawn face is detected by the face
detector) with controlled edits. They test the comparison logic and the integration; they say nothing
about detecting real AI-inserted people.
"""
import io
import os
import subprocess
import shutil
import tempfile

import cv2
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from conftest import demo_bytes
from truthlens.app import app
from truthlens.forensics.persons import analyze_people, detect_faces, head_box

client = TestClient(app)


def _rgb(name):
    return np.asarray(Image.open(io.BytesIO(demo_bytes(name))).convert("RGB"))


def _group(n=5, blur_index=None, seed=0):
    """n copies of the demo person's head on a noisy background; optionally one copy blurred."""
    src = _rgb("rally_original.jpg")
    face = detect_faces(src)[0]
    x0, y0, x1, y1 = head_box(face, src.shape[1], src.shape[0])
    head = src[y0:y1, x0:x1]
    hh, hw = head.shape[:2]
    rng = np.random.default_rng(seed)
    canvas = np.clip(rng.normal(120, 6, (hh + 40, n * (hw + 30) + 30, 3)), 0, 255).astype(np.uint8)
    for i in range(n):
        tile = cv2.GaussianBlur(head, (0, 0), 3.0) if i == blur_index else head
        canvas[20:20 + hh, 30 + i * (hw + 30):30 + i * (hw + 30) + hw] = tile
    return canvas


def test_detector_runs_offline_and_finds_each_copy():
    faces = detect_faces(_group(5))
    assert len(faces) == 5


def test_unedited_group_highlights_nobody():
    res = analyze_people(_group(5))
    assert res["compared"] and res["count"] == 5 and res["flagged"] == []
    assert res["experimental"] is True and res["affects_score"] is False


def test_blurred_person_is_highlighted_with_reasons():
    res = analyze_people(_group(5, blur_index=2))
    assert res["flagged"] == ["P3"]
    p3 = next(p for p in res["people"] if p["id"] == "P3")
    assert "sharpness" in p3["outliers"] and len(p3["outliers"]) >= 2 and p3["reasons"]


def test_fewer_than_three_people_are_never_highlighted():
    res = analyze_people(_group(2, blur_index=1))
    assert res["count"] == 2 and not res["compared"] and res["flagged"] == []


def test_people_block_is_reported_but_never_scored():
    rep = client.post("/api/demo?scenario=original").json()
    e = rep["evidence"][0]
    assert e["people"]["experimental"] and e["people"]["affects_score"] is False
    assert not any(f["category"].startswith("people") for x in rep["evidence"] for f in x["findings"])
    assert rep["overall"]["score"] == 0.11                                      # unchanged sample score
    assert any("people consistency" in l["detail"] for l in rep["log"] if l["step"] == "analysis.experimental")


def test_people_section_in_pdf_and_overlay_served():
    buf = io.BytesIO()
    Image.fromarray(_group(5, blur_index=2)).save(buf, "JPEG", quality=92)
    r = client.post("/api/analyze", files=[("files", ("group.jpg", buf.getvalue(), "image/jpeg"))])
    assert r.status_code == 200, r.text
    rep = r.json()
    pp = rep["evidence"][0]["people"]
    assert pp["flagged"] == ["P3"] and pp.get("artifact")
    assert client.get(f"/api/cases/{rep['case_id']}/artifacts/{pp['artifact']}").content[:4] == b"\x89PNG"
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    pdf = client.get(f"/api/cases/{rep['case_id']}/report.pdf").content
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf)
    text = subprocess.run(["pdftotext", f.name, "-"], capture_output=True, text=True).stdout
    os.unlink(f.name)
    assert "People consistency" in text and "EXPERIMENTAL" in text and "not part of the manipulation score" in text
    assert "P3 highlighted for review" in text
