"""Upload validation: decide what a file really is from its CONTENT, not its name.

1. Magic-byte signature (first bytes of the file) -> detected type and modality
2. The detected type must be one we support; unknown binaries are rejected
3. A decode check: images must open with Pillow (with a pixel limit against decompression
   bombs), video/audio must contain a matching stream according to ffprobe, text must be UTF-8
4. Size limits per file and per investigation
A mismatch between extension and content (e.g. a PNG renamed .jpg) is allowed but recorded.
"""
import json
import os
import subprocess
import tempfile

from PIL import Image
from .media import MediaError, run_media

MAX_FILE_BYTES = 150 * 1024 * 1024
MAX_TOTAL_BYTES = 300 * 1024 * 1024
MAX_FILES = 12
MAX_IMAGE_PIXELS = 80_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

EXT_MODALITY = {
    **{e: "image" for e in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")},
    **{e: "video" for e in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")},
    **{e: "audio" for e in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".opus")},
    **{e: "text" for e in (".txt", ".md", ".srt", ".vtt")},
}


class ValidationError(ValueError):
    def __init__(self, message, status_code=415):
        super().__init__(message)
        self.status_code = status_code


def sniff(data):
    """Return (detected_type, modality) from magic bytes, or (None, None)."""
    h = data[:64]
    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg", "image"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image"
    if h.startswith(b"BM"):
        return "bmp", "image"
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff", "image"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp", "image"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "wav", "audio"
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "avi", "video"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"M4A ", b"M4B ", b"F4A "):
            return "m4a", "audio"
        return "mp4/mov", "video"
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "mkv/webm", "video"
    if h.startswith(b"fLaC"):
        return "flac", "audio"
    if h.startswith(b"OggS"):
        return "ogg", "audio"
    if h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0 and (h[1] & 0x06) != 0):
        return "mp3", "audio"
    if len(h) > 1 and h[0] == 0xFF and (h[1] & 0xF6) == 0xF0:
        return "aac", "audio"
    if b"\x00" not in data[:4096]:
        try:
            data[:200000].decode("utf-8")
            return "text", "text"
        except UnicodeDecodeError:
            pass
    return None, None


def _ffprobe_streams(data, suffix):
    # delete=False + explicit close: on Windows an open NamedTemporaryFile is locked and ffprobe cannot read it
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        f.write(data)
        f.close()
        try:
            r = run_media("ffprobe", ["-v", "error", "-show_streams", "-of", "json", f.name], timeout=30)
            streams = json.loads(r.stdout or "{}").get("streams", [])
            if any(s.get("codec_type") == "video" for s in streams):
                run_media("ffmpeg", ["-v", "error", "-i", f.name, "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"], timeout=30)
        except MediaError as ex:
            raise ValidationError(str(ex), ex.status_code) from ex
        except (ValueError, TypeError) as ex:
            raise ValidationError("ffprobe returned invalid stream metadata") from ex
    finally:
        f.close()
        try:
            os.remove(f.name)
        except OSError:
            pass
    try:
        return [s.get("codec_type") for s in streams]
    except Exception:
        return []


def validate_file(filename, data):
    """Return {detected_type, modality, extension_modality, extension_mismatch, warnings}. Raises ValidationError."""
    if not data:
        raise ValidationError(f"{filename}: file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError(f"{filename}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    ext = os.path.splitext((filename or "").lower())[1]
    ext_mod = EXT_MODALITY.get(ext)
    dtype, mod = sniff(data)
    if not mod:
        raise ValidationError(f"{filename}: unrecognised or unsupported file content")
    warnings = []
    if mod == "image":
        try:
            import io
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except Image.DecompressionBombError:
            raise ValidationError(f"{filename}: image exceeds {MAX_IMAGE_PIXELS:,} pixels")
        except Exception as ex:
            raise ValidationError(f"{filename}: image could not be decoded ({type(ex).__name__})")
    elif mod in ("video", "audio"):
        streams = _ffprobe_streams(data, ext or ".bin")
        if mod == "video" and "video" not in streams:
            if "audio" in streams:
                mod = "audio"                 # e.g. an .mp4 container holding only audio
                warnings.append("Container has no video stream; treated as audio.")
            else:
                raise ValidationError(f"{filename}: no decodable video stream")
        if mod == "audio" and "audio" not in streams:
            raise ValidationError(f"{filename}: no decodable audio stream")
    mismatch = bool(ext_mod and ext_mod != mod) or (ext in (".jpg", ".jpeg") and dtype != "jpeg") or (ext == ".png" and dtype != "png")
    if mismatch:
        warnings.append(f"Extension '{ext}' does not match the file content ({dtype}). The content type is used.")
    return {"detected_type": dtype, "modality": mod, "extension_modality": ext_mod, "extension_mismatch": mismatch, "warnings": warnings}


def validate_batch(files):
    """files: list of (name, bytes). Raises ValidationError for batch-level limits."""
    if len(files) > MAX_FILES:
        raise ValidationError(f"At most {MAX_FILES} files per investigation")
    total = sum(len(b) for _, b in files)
    if total > MAX_TOTAL_BYTES:
        raise ValidationError(f"Total upload larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB")
