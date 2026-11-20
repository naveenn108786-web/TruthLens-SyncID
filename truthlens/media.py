"""Bounded local FFmpeg operations; originals are never overwritten."""
import os
from pathlib import Path
import subprocess
import threading
import uuid


class MediaError(ValueError):
    def __init__(self, message, status_code=415):
        super().__init__(message)
        self.status_code = status_code


def run_media(tool, args, timeout):
    binary = os.environ.get("TRUTHLENS_" + tool.upper(), tool)
    try:
        result = subprocess.run([binary, "-nostdin", *args] if tool == "ffmpeg" else [binary, *args],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as ex:
        raise MediaError(f"{tool} is unavailable to the server. Install FFmpeg/FFprobe and restart TruthLens, or configure TRUTHLENS_{tool.upper()}.", 503) from ex
    except subprocess.TimeoutExpired as ex:
        raise MediaError(f"{tool} exceeded the {timeout}-second processing limit. Try a shorter clip or a supported codec.", 422) from ex
    except OSError as ex:
        raise MediaError(f"{tool} could not start ({type(ex).__name__}). Check the server media-tool configuration.", 503) from ex
    if result.returncode:
        # Do not pretend that probing metadata proves the stream can be decoded.
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1][:200] if detail else "corrupted stream or unsupported codec"
        raise MediaError(f"{tool} could not process this media: {reason}")
    return result


_preview_lock = threading.Lock()


def compatible_preview(original, output):
    """On-demand browser-compatible derivative at source resolution (even padding only)."""
    output = Path(output)
    with _preview_lock:
        if output.is_file():
            return str(output)
        temporary = output.with_name(output.stem + "." + uuid.uuid4().hex + ".mp4")
        try:
            run_media("ffmpeg", ["-y", "-v", "error", "-i", str(original), "-map", "0:v:0", "-map", "0:a:0?",
                                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                                "-map_metadata", "-1", str(temporary)], timeout=120)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return str(output)
