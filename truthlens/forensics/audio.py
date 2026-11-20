"""Audio and voice analysis.

The audio is split into short windows (0.5 s). For each window we measure:
- background noise floor: real recordings carry a steady room tone; spliced-in or synthetic
  speech often has a different (or perfectly silent, "digital zero") background
- spectral shape (centroid / brightness): a different microphone or a voice generator
  changes the tone colour of the voice
- pitch micro-variation (jitter): human voices wobble slightly from one moment to the next;
  many synthetic voices are unnaturally steady
Windows that differ strongly from the rest of the recording are grouped into flagged
segments with start and end times. Hard boundaries between segments are reported as possible
splice points.
"""
import os
import subprocess

import numpy as np
from PIL import Image, ImageDraw

from .common import finding, robust_z, heat_colormap

SR = 16000
FRAME = 400     # 25 ms
HOP = 160       # 10 ms
WIN = 0.5       # analysis window, seconds
WIN_HOP = 0.25


def load_audio(path):
    """Decode any audio/video file to 16 kHz mono float32 with ffmpeg."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def probe(path):
    try:
        import json
        r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def frame_features(x):
    n = 1 + max(0, (len(x) - FRAME) // HOP)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(FRAME)[None, :]
    rms = np.sqrt((x[idx] ** 2).mean(axis=1))
    db = 20 * np.log10(rms + 1e-9)
    spec = np.abs(np.fft.rfft(frames, axis=1)) + 1e-9
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    centroid = (spec * freqs).sum(axis=1) / spec.sum(axis=1)
    flatness = np.exp(np.log(spec).mean(axis=1)) / spec.mean(axis=1)
    # pitch via autocorrelation on 40 ms frames (60-400 Hz)
    f0 = np.full(n, np.nan)
    L = 640
    lo, hi = SR // 400, SR // 60
    for i in range(n):
        s = i * HOP
        seg = x[s:s + L]
        if len(seg) < L or db[i] < -35:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, "full")[L - 1:]
        if ac[0] <= 0:
            continue
        lag = lo + np.argmax(ac[lo:hi])
        if ac[lag] / ac[0] > 0.45:          # voiced
            # parabolic interpolation for sub-sample precision
            if 1 <= lag < len(ac) - 1:
                a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
                lag = lag + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
            f0[i] = SR / lag
    return {"db": db, "centroid": centroid, "flatness": flatness, "f0": f0}


def window_stats(ff, duration):
    fps = SR / HOP
    rows = []
    t = 0.0
    while t + WIN <= duration + 1e-6:
        a, b = int(t * fps), int((t + WIN) * fps)
        db = ff["db"][a:b]
        f0 = ff["f0"][a:b]
        voiced = ~np.isnan(f0)
        jit = np.nan
        if voiced.sum() > 6:
            v = f0[voiced]
            d = np.abs(np.diff(v)) / v[1:]
            d = d[d < 0.08]                 # ignore octave jumps / syllable boundaries
            if len(d) > 3:
                jit = float(np.median(d))
        rows.append({
            "start": round(t, 2), "end": round(t + WIN, 2),
            "floor_db": float(np.percentile(db, 10)) if len(db) else -120,
            "digital_silence": float((db < -90).mean()) if len(db) else 0,
            "centroid": float(np.median(ff["centroid"][a:b][db > -35])) if (db > -35).any() else np.nan,
            "jitter": jit,
        })
        t += WIN_HOP
    return rows


def _segments(flags, rows):
    segs, cur = [], None
    for i, f in enumerate(flags):
        if f and cur is None:
            cur = [i, i]
        elif f:
            cur[1] = i
        elif cur is not None:
            segs.append(cur)
            cur = None
    if cur is not None:
        segs.append(cur)
    out = []
    for a, b in segs:
        # windows overlap by half, so trim one hop at each inner edge for a tighter estimate
        start = rows[a]["start"] + (WIN_HOP if a > 0 and b > a else 0)
        end = rows[b]["end"] - (WIN_HOP if b < len(rows) - 1 and b > a else 0)
        out.append((round(start, 2), round(end, 2), a, b))
    return out


def waveform_envelope(x, bins=600):
    """Min/max of the analysed samples in equal time bins: the waveform the interface draws (same decoded signal as the analysis)."""
    if not len(x):
        return {"bins": 0, "min": [], "max": []}
    n = min(bins, len(x))
    edges = np.linspace(0, len(x), n + 1).astype(int)
    lo = [round(float(x[a:b].min()), 3) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
    hi = [round(float(x[a:b].max()), 3) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
    return {"bins": n, "min": lo, "max": hi}


def spectrogram_png(x, path, segments, width=1100, height=260):
    n = 1 + max(0, (len(x) - 512) // 128)
    idx = np.arange(512)[None, :] + 128 * np.arange(n)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * np.hanning(512)[None, :], axis=1)).T[::-1]   # freq x time
    S = 20 * np.log10(S + 1e-6)
    S = np.clip((S - (S.max() - 80)) / 80, 0, 1)
    img = Image.fromarray(heat_colormap(S)).resize((width, height))
    d = ImageDraw.Draw(img)
    dur = len(x) / SR
    for label, a, b in segments:
        xa, xb = int(a / dur * width), int(b / dur * width)
        d.rectangle([xa, 1, xb, height - 2], outline=(0, 255, 200), width=3)
        d.rectangle([xa, 0, xa + 8 * len(label) + 8, 16], fill=(0, 255, 200))
        d.text((xa + 4, 2), label, fill=(0, 0, 0))
    img.save(path, "PNG")


def analyze_audio(path, out_dir, key, modality_label="audio"):
    info = probe(path)
    x = load_audio(path)
    duration = len(x) / SR
    meta = {"duration_s": round(duration, 2), "sample_rate_analyzed": SR}
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            meta.update({"codec": s.get("codec_name"), "original_sample_rate": s.get("sample_rate"),
                         "channels": s.get("channels")})
            break
    tags = (info.get("format", {}) or {}).get("tags", {}) or {}
    meta["container_tags"] = {k: v for k, v in tags.items()}
    meta["waveform"] = waveform_envelope(x)
    limitations = ["Indicators are acoustic heuristics, not a trained voice-clone classifier; high-quality clones recorded through a real microphone can pass.",
                   "Music, background speech or heavy compression (phone calls, WhatsApp voice notes) can mask or mimic these signals."]
    findings = []
    if duration < 2.0 or np.abs(x).max() < 1e-4:
        limitations.append("Recording too short or silent for segment analysis.")
        return {"modality": modality_label, "metadata": meta, "findings": findings, "artifacts": {}, "limitations": limitations}

    ff = frame_features(x)
    rows = window_stats(ff, duration)
    floor = np.array([r["floor_db"] for r in rows])
    cent = np.array([r["centroid"] for r in rows])
    jit = np.array([r["jitter"] for r in rows])
    dsil = np.array([r["digital_silence"] for r in rows])
    if len(dsil) > 2:
        dsil[0] = dsil[-1] = 0.0     # leading / trailing digital silence is normal padding (recorders, MP3 encoder delay)

    med_floor = float(np.median(floor))
    floor_flag = (np.abs(floor - med_floor) > 15) | (dsil > 0.05)
    ok = ~np.isnan(cent)
    cz = np.zeros_like(cent)
    if ok.sum() > 5:
        cz[ok] = robust_z(cent[ok])
    tone_flag = np.abs(cz) > 4.0
    okj = ~np.isnan(jit)
    jz = np.zeros_like(jit)
    med_jit = float(np.nanmedian(jit)) if okj.any() else np.nan
    if okj.sum() > 5:
        jz[okj] = robust_z(np.log(jit[okj] + 1e-5))
    steady_flag = jz < -3.0

    meta["noise_floor_db_median"] = round(med_floor, 1)
    meta["pitch_jitter_median_pct"] = round(med_jit * 100, 2) if not np.isnan(med_jit) else None
    meta["windows"] = [{"t": r["start"], "floor_db": round(r["floor_db"], 1),
                        "jitter_pct": None if np.isnan(r["jitter"]) else round(r["jitter"] * 100, 2)} for r in rows]

    combined = floor_flag | tone_flag | steady_flag
    seg_labels = []
    for start, end, a, b in _segments(combined, rows):
        sl = slice(a, b + 1)
        reasons, score = [], 0.0
        if floor_flag[sl].mean() > 0.4:
            fl = float(np.median(floor[sl]))
            if dsil[sl].max() > 0.05:
                reasons.append(f"background drops to digital silence (true zero) for up to {dsil[sl].max():.0%} of the window, while the rest of the recording has steady room tone around {med_floor:.0f} dB")
            else:
                reasons.append(f"background noise floor is {fl:.0f} dB versus {med_floor:.0f} dB in the rest of the recording")
            score += 0.35
        if tone_flag[sl].mean() > 0.4:
            reasons.append(f"voice brightness (spectral centroid) shifts to {np.nanmedian(cent[sl]):.0f} Hz versus {np.nanmedian(cent):.0f} Hz elsewhere")
            score += 0.2
        if steady_flag[sl].mean() > 0.4:
            reasons.append(f"pitch micro-variation is only {np.nanmedian(jit[sl]) * 100:.2f}% versus {med_jit * 100:.2f}% elsewhere (unnaturally steady voice)")
            score += 0.3
        if not reasons:
            continue
        # voice brightness alone varies with vowels and emphasis: only trust it over longer spans
        if len(reasons) == 1 and tone_flag[sl].mean() > 0.4 and not floor_flag[sl].any() and (end - start) < 1.0:
            continue
        if len(reasons) >= 2:
            score += 0.1          # independent cues agreeing
        f = finding(modality_label, "synthetic-or-spliced-audio",
                    "Acoustically inconsistent segment" + (" (possible synthetic voice)" if steady_flag[sl].mean() > 0.4 else ""),
                    f"Between {start:.2f}s and {end:.2f}s the " + "; and the ".join(reasons) + ". "
                    "A segment recorded in a different place, or generated by a voice model, typically differs from its surroundings like this.",
                    min(score, 0.9), {"type": "segment", "start": start, "end": end},
                    ["Speaker moved closer to / away from the microphone", "Noise suppression switching on/off in a call app",
                     "Background noise genuinely changed (door closed, fan stopped)"],
                    artifact=f"{key}_spectrogram")
        findings.append(f)
        seg_labels.append((f["id"], start, end))
        # boundaries of a flagged segment that start/end abruptly are possible splice points
        for edge in (start, end):
            if 0.3 < edge < duration - 0.3:
                i = int(edge * SR)
                pre = np.sqrt(np.mean(x[max(0, i - 4000):i] ** 2)) + 1e-9
                post = np.sqrt(np.mean(x[i:i + 4000] ** 2)) + 1e-9
                jump = abs(20 * np.log10(post / pre))
                meta.setdefault("possible_splice_points", []).append({"time": edge, "level_jump_db": round(float(jump), 1)})

    if len(findings) > 3:
        # Many "inconsistent" segments means there is no stable baseline to compare against
        # (music, a produced soundtrack, several speakers or sources). Per-segment flags are unreliable then.
        segs = [(f["location"]["start"], f["location"]["end"]) for f in findings]
        meta["variable_segments"] = [{"start": a, "end": b} for a, b in segs]
        meta.pop("possible_splice_points", None)
        findings = [finding(modality_label, "audio-variability", "Highly variable audio: segment checks not reliable here",
                            f"{len(segs)} segments differ from the rest of the recording, so there is no stable background to compare against. "
                            "This is typical of music, produced soundtracks or recordings mixing several sources. Individual segments are not reported as anomalies.",
                            0.1, {"type": "file"},
                            ["Music or produced soundtrack", "Several speakers or microphones", "Genuinely changing environment"],
                            artifact=f"{key}_spectrogram")]
        limitations.append("Segment-level audio checks assume one recording with steady background noise; this file does not have one.")
        seg_labels = []      # do not draw boxes for segments that are no longer reported
    spectrogram_png(x, os.path.join(out_dir, f"{key}_spectrogram.png"), seg_labels)
    return {"modality": modality_label, "metadata": meta, "findings": findings,
            "artifacts": {f"{key}_spectrogram": f"{key}_spectrogram.png"}, "limitations": limitations}
