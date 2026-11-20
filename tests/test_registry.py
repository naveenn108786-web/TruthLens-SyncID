import io

from PIL import Image

from conftest import demo_bytes
from truthlens import registry


def _reencode(data, quality=75, scale=1.0):
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def test_register_assigns_uuid_and_is_idempotent():
    data = demo_bytes("rally_original.jpg")
    a = registry.add("rally_original.jpg", data, "test")
    b = registry.add("copy.jpg", data, "test")
    assert a["uuid"] == b["uuid"] and len(a["uuid"]) == 36
    assert a["c2pa"] in ("valid", "valid_untrusted", "none")


def test_exact_and_perceptual_matching(tmp_path):
    data = demo_bytes("rally_original.jpg")
    reg = registry.add("rally_original.jpg", data, "test")
    p = tmp_path / "same.jpg"
    p.write_bytes(data)
    look = registry.lookup(str(p), registry.sha256_bytes(data))
    assert look["exact"]["uuid"] == reg["uuid"]

    # re-encoded and resized: different bytes, same picture -> similar, not exact
    re = _reencode(data, quality=70, scale=0.5)
    p2 = tmp_path / "re.jpg"
    p2.write_bytes(re)
    look = registry.lookup(str(p2), registry.sha256_bytes(re))
    assert look["exact"] is None
    assert look["similar"] and look["similar"][0][0]["uuid"] == reg["uuid"]


def test_unrelated_image_not_matched(tmp_path):
    registry.add("rally_original.jpg", demo_bytes("rally_original.jpg"), "test")
    data = demo_bytes("ai_portrait.png")
    p = tmp_path / "x.png"
    p.write_bytes(data)
    look = registry.lookup(str(p), registry.sha256_bytes(data))
    assert look["exact"] is None and not look["similar"]
