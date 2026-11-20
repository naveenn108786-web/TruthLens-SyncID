"""Real FFmpeg fixtures plus explicit fault injection; no fabricated detector outputs."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from truthlens import db
from truthlens.app import app
from truthlens import validation


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    root = tmp_path_factory.mktemp("video-codecs")
    files = {}
    for name, codec, audio in (("h264.mp4", "libx264", False), ("mpeg4.mov", "mpeg4", True), ("ffv1.mkv", "ffv1", False)):
        path = root / name
        args = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10"]
        if audio:
            args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000"]
        args += ["-t", "1.2", "-c:v", codec]
        if codec != "ffv1":
            args += ["-pix_fmt", "yuv420p"]
        if audio:
            args += ["-c:a", "aac"]
        args += [str(path)]
        subprocess.run(args, capture_output=True, check=True, timeout=30)
        files[name] = path.read_bytes()
    return files


def upload(name, content):
    return TestClient(app).post("/api/analyze", files=[("files", (name, content))])


@pytest.mark.parametrize("error,expected", [(FileNotFoundError("ffprobe"), 503), (subprocess.TimeoutExpired("ffprobe", 30), 422)])
def test_media_process_errors_are_meaningful_not_http500(videos, monkeypatch, error, expected):
    before = len(db.list_investigations())
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr("truthlens.media.subprocess.run", fail)
    response = upload("clip.mp4", videos["h264.mp4"])
    assert response.status_code == expected
    assert "ffprobe" in response.json()["detail"]
    assert len(db.list_investigations()) == before


@pytest.mark.parametrize("name,audio", [("h264.mp4", False), ("mpeg4.mov", True), ("ffv1.mkv", False)])
def test_common_containers_preserve_original_and_sample_frames(videos, name, audio):
    response = upload(name, videos[name])
    assert response.status_code == 200, response.text
    report = response.json()
    video = next(e for e in report["evidence"] if e["modality"] == "video")
    assert not any(l.startswith("Analysis failed:") for l in video["limitations"])
    assert video["sha256"] == hashlib.sha256(videos[name]).hexdigest()
    assert video["metadata"]["width"] == 320 and video["metadata"]["height"] == 180
    assert video["metadata"]["has_audio"] is audio
    assert video["metadata"]["frames"]
    route = f"/api/cases/{report['case_id']}/evidence/{video['id']}"
    original = TestClient(app).get(route)
    assert original.content == videos[name]
    assert original.headers["content-disposition"].startswith("inline")
    ranged = TestClient(app).get(route, headers={"Range": "bytes=0-63"})
    assert ranged.status_code == 206 and ranged.content == videos[name][:64]


def test_compatible_preview_is_separate_and_retains_resolution(videos):
    report = upload("ffv1.mkv", videos["ffv1.mkv"]).json()
    video = report["evidence"][0]
    route = f"/api/cases/{report['case_id']}/preview/{video['id']}"
    client = TestClient(app)
    response = client.get(route)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("video/mp4")
    ranged = client.get(route, headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206 and ranged.content == response.content[:32]
    from truthlens.pipeline import case_dir
    preview = next((Path(case_dir(report["case_id"])) / "artifacts").glob("*_preview.mp4"))
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(preview)], capture_output=True, text=True, check=True).stdout)
    stream = info["streams"][0]
    assert stream["codec_name"] == "h264" and stream["width"] == 320 and stream["height"] == 180
    assert client.get(f"/api/cases/{report['case_id']}/evidence/{video['id']}").content == videos["ffv1.mkv"]


def test_corrupted_video_is_rejected_cleanly():
    response = upload("broken.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100)
    assert response.status_code == 415 and "ffprobe" in response.json()["detail"]


def test_unsupported_decoder_is_rejected_with_detail(videos, monkeypatch):
    real = subprocess.run
    def unavailable_decoder(args, **kwargs):
        if "-frames:v" in args:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Decoder not found for video stream")
        return real(args, **kwargs)
    monkeypatch.setattr("truthlens.media.subprocess.run", unavailable_decoder)
    response = upload("unsupported.mp4", videos["h264.mp4"])
    assert response.status_code == 415 and "Decoder not found" in response.json()["detail"]


def test_upload_limits_are_enforced(videos, monkeypatch):
    monkeypatch.setattr("truthlens.app.MAX_UPLOAD", len(videos["h264.mp4"]) - 1)
    response = upload("large.mp4", videos["h264.mp4"])
    assert response.status_code == 413
    monkeypatch.setattr(validation, "MAX_FILE_BYTES", len(videos["h264.mp4"]) - 1)
    with pytest.raises(validation.ValidationError, match="larger"):
        validation.validate_file("large.mp4", videos["h264.mp4"])


def test_renamed_video_is_served_as_video(videos):
    report = upload("misnamed.jpg", videos["h264.mp4"]).json()
    item = report["evidence"][0]
    response = TestClient(app).get(f"/api/cases/{report['case_id']}/evidence/{item['id']}")
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.content == videos["h264.mp4"]


def test_audio_failure_does_not_discard_video_results(videos, monkeypatch):
    def timeout(*args):
        raise subprocess.TimeoutExpired("ffmpeg", 60)
    monkeypatch.setattr("truthlens.pipeline.extract_audio_track", timeout)
    response = upload("mpeg4.mov", videos["mpeg4.mov"])
    assert response.status_code == 200
    report = response.json()
    item = report["evidence"][0]
    assert report["analysis_status"] == "partial" and item["analysis_status"] == "partial"
    assert item["metadata"]["frames"] and item["sha256"] == hashlib.sha256(videos["mpeg4.mov"]).hexdigest()
    assert any("audio extraction/analysis" in limit for limit in item["limitations"])
    client = TestClient(app)
    assert client.get(f"/api/cases/{report['case_id']}/evidence/{item['id']}").content == videos["mpeg4.mov"]
    assert client.get(f"/api/cases/{report['case_id']}/report.pdf").status_code == 200


def test_sample_interval_is_configurable_and_timestamps_are_recorded(videos, monkeypatch):
    monkeypatch.setenv("TRUTHLENS_VIDEO_SAMPLE_INTERVAL", "0.2")
    report = upload("h264.mp4", videos["h264.mp4"]).json()
    item = report["evidence"][0]
    metadata = item["metadata"]
    assert metadata["requested_sample_interval_s"] == 0.2
    assert len(metadata["frames"]) >= 5
    assert len(metadata["frames"]) == metadata["frames_sampled"]
    assert all(0 <= frame["t"] <= metadata["duration_s"] for frame in metadata["frames"])


def test_analysis_decoder_failure_preserves_accepted_evidence(videos, monkeypatch):
    from truthlens.media import MediaError
    def fail(*args):
        raise MediaError("OpenCV cannot decode this stream", 422)
    monkeypatch.setattr("truthlens.pipeline.analyze_video", fail)
    report = upload("h264.mp4", videos["h264.mp4"]).json()
    assert report["analysis_status"] == "partial"
    item = report["evidence"][0]
    assert item["analysis_status"] == "failed"
    assert TestClient(app).get(f"/api/cases/{report['case_id']}/evidence/{item['id']}").content == videos["h264.mp4"]


def test_probe_temporary_file_is_removed_on_failure(videos, monkeypatch):
    created = []
    real = validation.tempfile.NamedTemporaryFile
    def temporary(*args, **kwargs):
        result = real(*args, **kwargs)
        created.append(result.name)
        return result
    def fail(*args, **kwargs):
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr(validation.tempfile, "NamedTemporaryFile", temporary)
    monkeypatch.setattr("truthlens.media.subprocess.run", fail)
    assert upload("h264.mp4", videos["h264.mp4"]).status_code == 503
    assert created and all(not Path(path).exists() for path in created)


def test_preview_timeout_preserves_original(videos, monkeypatch):
    report = upload("h264.mp4", videos["h264.mp4"]).json()
    item = report["evidence"][0]
    real = subprocess.run
    def timeout(args, **kwargs):
        if "-crf" in args:
            raise subprocess.TimeoutExpired("ffmpeg", 120)
        return real(args, **kwargs)
    monkeypatch.setattr("truthlens.media.subprocess.run", timeout)
    client = TestClient(app)
    response = client.get(f"/api/cases/{report['case_id']}/preview/{item['id']}")
    assert response.status_code == 422 and "120-second" in response.json()["detail"]
    assert client.get(f"/api/cases/{report['case_id']}/evidence/{item['id']}").content == videos["h264.mp4"]
