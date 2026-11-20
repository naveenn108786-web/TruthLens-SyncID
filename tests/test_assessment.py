import copy

import pytest
from fastapi.testclient import TestClient

from truthlens.assessment import LABELS, assess_image, build_assessments
from truthlens.app import app


@pytest.mark.parametrize("result,expected", [
    ({"metadata": {"text_chunks": {"Software": "Stable Diffusion"}}}, "possible"),
    ({"metadata": {"text_chunks": {"parameters": "made with AI"}}}, "possible"),
    ({"metadata": {"exif": {"Software": "C2PA generated report"}}}, "no_evidence"),
    ({"metadata": {"exif": {"Make": "Nikon", "Model": "D70"}}, "generator": {"raw_score": 0.1}}, "no_evidence"),
    ({"metadata": {"exif": {}, "text_chunks": {}}, "generator": {"raw_score": 0.1}}, "inconclusive"),
    ({"limitations": ["Analysis failed: decoder error"]}, "inconclusive"),
    ({"provenance": {"c2pa": {"status": "valid_untrusted", "ai_generated": True}}}, "evidence_detected"),
    ({"provenance": {"c2pa": {"status": "valid", "ai_generated": True}}}, "evidence_detected"),
    ({"provenance": {"c2pa": {"status": "invalid", "ai_generated": True}}}, "possible"),
    ({"findings": [{"id": "F1", "category": "synthetic-generation", "experimental": True, "explanation": "Measured periodic frequency cue"}]}, "possible"),
    ({"findings": [{"id": "F1", "category": "noise-absent", "score": 0.4}]}, "inconclusive"),
])
def test_evidence_statuses(result, expected):
    before = copy.deepcopy(result)
    assessment = assess_image(result)
    assert assessment["status"] == expected and assessment["label"] == LABELS[expected]
    assert assessment["confidence"] is None and not assessment["confidence_calibrated"]
    assert assessment["model_status"] == "not_configured"
    if expected in ("possible", "evidence_detected"):
        assert assessment["evidence"]
    assert result == before  # assessment never edits forensic findings or scores


def test_metadata_removal_does_not_turn_ai_into_verified_camera():
    declared = assess_image({"metadata": {"text_chunks": {"Software": "Stable Diffusion"}}})
    stripped = assess_image({"metadata": {"exif": {}, "text_chunks": {}}})
    assert declared["status"] == "possible" and stripped["status"] == "inconclusive"
    assert any("not proof of camera capture" in limit for limit in stripped["limitations"])


def test_case_categories_and_mixed_evidence_are_separate():
    evidence = [{"id": "E1", "filename": "camera.jpg", "modality": "image"},
                {"id": "E2", "filename": "unknown.png", "modality": "image"}]
    overall = {"score": 0.05, "label": "No strong indicators of manipulation found"}
    before = copy.deepcopy(overall)
    categories = build_assessments(evidence, {"E1": {"metadata": {"exif": {"Make": "Nikon"}}}}, overall)
    assert set(categories) == {"manipulation", "ai_generation", "content_provenance", "sync_id"}
    assert categories["ai_generation"]["status"] == "inconclusive"
    assert categories["manipulation"]["indicator"] == 0.05 and overall == before
    assert categories["sync_id"]["status"] == "not_assessed"
    assert categories["content_provenance"]["independently_verified_origin"] == "not_established"
    assert all(i["issuer_trust"] == "not_established" for i in categories["content_provenance"]["items"])


def test_no_images_are_not_assessed():
    result = build_assessments([], {}, {"score": 0.05, "label": "Low"})
    assert result["ai_generation"]["status"] == "not_assessed"


def test_new_report_persists_assessment_and_pdf_preserves_it():
    client = TestClient(app)
    report = client.post("/api/demo?scenario=original").json()
    assert report["overall"]["score"] == 0.11  # established demo regression value, unchanged
    assessment = report["assessments"]["ai_generation"]
    assert assessment["confidence"] is None
    saved = client.get("/api/cases/" + report["case_id"]).json()
    assert saved["assessments"] == report["assessments"]
    pdf = client.get("/api/cases/" + report["case_id"] + "/report.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    # Optional rendering library is for PDF QA, not an application requirement.
    try:
        import fitz
    except ImportError:
        return
    with fitz.open(stream=pdf.content, filetype="pdf") as document:
        text = " ".join(page.get_text() for page in document)
    assert "AI-generation assessment" in text and assessment["label"] in text
    assert "camera capture" in text and "Sync ID: not assessed" in text


def test_assessment_does_not_feed_existing_scoring(monkeypatch):
    client = TestClient(app)
    first = client.post("/api/demo?scenario=synthetic").json()
    monkeypatch.setattr("truthlens.pipeline.build_assessments", lambda *args: {"test_only": "not a detector result"})
    second = client.post("/api/demo?scenario=synthetic").json()
    assert first["overall"] == second["overall"]
    signatures = lambda report: sorted((f["category"], f["indicator"], f["score"]) for e in report["evidence"] for f in e["findings"] if f["category"] != "history")
    assert signatures(first) == signatures(second)
