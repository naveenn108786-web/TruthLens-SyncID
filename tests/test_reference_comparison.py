"""Controlled pixel edits test localization; they are not claimed AI-generated objects."""
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest
from fastapi.testclient import TestClient

from truthlens.app import app
from truthlens.forensics.comparison import compare_images
from truthlens.forensics.image import metadata_findings, read_metadata


@pytest.fixture
def original():
    rng = np.random.default_rng(51)
    image = Image.fromarray(np.clip(rng.normal(120, 4, (180, 320, 3)), 0, 255).astype(np.uint8))
    ImageDraw.Draw(image).rectangle((35, 50, 85, 120), fill=(50, 190, 80))
    return image


def encoded(image):
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.mark.parametrize("operation", ["added_region", "removed_region", "inpainted_region", "normal_edit"])
def test_controlled_changes_are_localized_without_ai_attribution(tmp_path, original, operation):
    changed = original.copy()
    if operation == "added_region":
        expected = [190, 60, 65, 65]
        ImageDraw.Draw(changed).ellipse((190, 60, 255, 125), fill=(240, 20, 30))
    elif operation == "removed_region":
        expected = [35, 50, 50, 70]
        ImageDraw.Draw(changed).rectangle((35, 50, 85, 120), fill=(120, 120, 120))
    elif operation == "inpainted_region":
        expected = [35, 50, 50, 70]
        mask = np.zeros((180, 320), np.uint8)
        mask[45:126, 30:91] = 255
        changed = Image.fromarray(cv2.inpaint(np.asarray(changed), mask, 3, cv2.INPAINT_TELEA))
    else:
        expected = [140, 50, 70, 70]
        ImageDraw.Draw(changed).rectangle((140, 50, 210, 120), fill=(180, 180, 180))
    a, b = tmp_path / "reference.png", tmp_path / "submitted.png"
    a.write_bytes(encoded(original)); b.write_bytes(encoded(changed))
    result = compare_images(str(a), str(b), str(tmp_path), "E1", "E2")
    assert result["status"] == "compared" and result["significant_change_fraction"] > 0
    x, y, w, h = expected
    assert any(min(x+w, rx+rw) > max(x, rx) and min(y+h, ry+rh) > max(y, ry) for rx, ry, rw, rh in result["regions"])
    assert result["ai_object_attribution"] == "not_established" and result["confidence"] is None
    assert all(f["score"] == 0 and f["category"] == "reference-difference" for f in result["findings"])
    assert (tmp_path / result["artifacts"]["E1_reference_diff"]).is_file()


def test_identical_and_incomparable_references(tmp_path, original):
    a = tmp_path / "original.png"; a.write_bytes(encoded(original))
    same = compare_images(str(a), str(a), str(tmp_path), "E1", "E2")
    assert same["significant_change_fraction"] == 0 and same["regions"] == []
    b = tmp_path / "cropped.png"; b.write_bytes(encoded(original.crop((0, 0, 150, 180))))
    mismatch = compare_images(str(a), str(b), str(tmp_path), "E3", "E2")
    assert mismatch["status"] == "not_comparable" and mismatch["findings"] == []


def test_explicit_reference_api_preserves_both_files_and_region_evidence(original):
    changed = original.copy()
    ImageDraw.Draw(changed).rectangle((190, 60, 255, 125), fill=(255, 40, 40))
    a, b = encoded(original), encoded(changed)
    client = TestClient(app)
    baseline = client.post("/api/analyze", files=[("files", ("changed.png", b))]).json()
    response = client.post("/api/analyze", files=[("files", ("changed.png", b)), ("reference_image", ("reference.png", a))])
    assert response.status_code == 200, response.text
    report = response.json()
    submitted, reference = report["evidence"]
    assert reference["role"] == "reference"
    assert report["overall"] == baseline["overall"]
    assert len(report["assessments"]["ai_generation"]["items"]) == 1
    assert submitted["reference_comparison"]["reference_id"] == reference["id"]
    assert submitted["reference_comparison"]["regions"]
    assert any(row["investigation"] == "Supplied reference comparison" and "changed region" in row["result"] for row in report["investigation"])
    for item, expected in ((submitted, b), (reference, a)):
        assert client.get(f"/api/cases/{report['case_id']}/evidence/{item['id']}").content == expected
    assert client.get(f"/api/cases/{report['case_id']}/report.pdf").status_code == 200


@pytest.mark.parametrize("text", ["C2PA Content Credentials", "generated camera preview", "flux capacitor", "imagen camera archive"])
def test_generic_camera_workflow_words_are_not_ai_generation_evidence(text):
    result = metadata_findings({"exif": {"Software": text}, "text_chunks": {}, "device": "camera"})
    assert not any(f["category"] == "synthetic-generation" for f in result)


def test_explicit_tool_declaration_remains_reported_as_unverified():
    result = metadata_findings({"exif": {"Software": "Stable Diffusion"}, "text_chunks": {}, "device": None})
    ai = [f for f in result if f["category"] == "synthetic-generation"]
    assert len(ai) == 1 and ai[0]["score"] == .85 and "unverified" in ai[0]["indicator"]


@pytest.mark.parametrize("name", ["Nikon_D70.jpg", "Canon_40D.jpg", "Sony_HDR-HC3.jpg"])
def test_source_documented_camera_metadata_is_not_classified_ai(name):
    path = Path(__file__).resolve().parents[1] / "samples" / "real" / "assessment-evaluation" / name
    if not path.exists():
        pytest.skip("Optional downloaded camera sample absent; run samples/eval_assessment.py --fetch")
    with Image.open(path) as image:
        findings = metadata_findings(read_metadata(image))
    assert not any(f["category"] == "synthetic-generation" for f in findings)
