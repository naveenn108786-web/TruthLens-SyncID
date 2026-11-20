import io

import pytest
from PIL import Image

from conftest import demo_bytes
from truthlens.validation import ValidationError, sniff, validate_batch, validate_file


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("name,expected", [
    ("rally_original.jpg", ("jpeg", "image")),
    ("ai_portrait.png", ("png", "image")),
    ("viral_clip.mp4", ("mp4/mov", "video")),
    ("speech_spliced.wav", ("wav", "audio")),
    ("caption.txt", ("text", "text")),
])
def test_sniff_detects_demo_files(name, expected):
    assert sniff(demo_bytes(name)) == expected


def test_rejects_unknown_binary():
    with pytest.raises(ValidationError):
        validate_file("evil.jpg", b"\x00\x01\x02\x03" * 100)


def test_rejects_empty():
    with pytest.raises(ValidationError):
        validate_file("empty.png", b"")


def test_rejects_corrupt_image_with_valid_magic():
    with pytest.raises(ValidationError):
        validate_file("broken.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 200)


def test_extension_mismatch_is_recorded_not_rejected():
    r = validate_file("photo.jpg", _png())
    assert r["modality"] == "image" and r["detected_type"] == "png"
    assert r["extension_mismatch"] and r["warnings"]


def test_video_and_audio_streams_checked():
    assert validate_file("clip.mp4", demo_bytes("viral_clip.mp4"))["modality"] == "video"
    assert validate_file("a.wav", demo_bytes("speech_spliced.wav"))["modality"] == "audio"


def test_batch_limits():
    with pytest.raises(ValidationError):
        validate_batch([("a.txt", b"x")] * 50)
