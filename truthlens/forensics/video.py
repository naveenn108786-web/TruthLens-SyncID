"""Video forensics.

1. Temporal consistency: measure how much each frame differs from the previous one.
   A sudden spike means a hard cut. In footage claimed to be one continuous "unedited" take,
   cuts (especially two close together, i.e. an inserted segment) are worth a human look.
2. Per-frame spatial checks: sample frames and reuse the image noise-consistency check.
   A face-swap or pasted region tends to lack the sensor noise of the rest of the frame,
   and the same region stays flagged across many frames.
3. Container metadata (creation time, encoder) from ffprobe.
The audio track is extracted and analysed separately by the audio module.
"""
import json
import os
import subprocess
import math
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .common import finding, robust_z
from .image import find_anomalous_regions, noise_map
from ..media import run_media, MediaError

MAX_FRAMES = 9000        # ~6 minutes at 25 fps
SAMPLE_EVERY_S = 0.5     # spatial check cadence
MAX_SAMPLES = 40
MAX_SPATIAL_SIDE = 1280


def probe(path):
    r = run_media("ffprobe", ["-v", "error", "-show_format", "-show_streams", "-of", "json", path], timeout=20)
    return json.loads(r.stdout or "{}")


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / (aw * ah + bw * bh - inter + 1e-9)


def _thumb_with_box(rgb, boxes, path, width=480):
    img = Image.fromarray(rgb)
    sc = width / img.width
    img = img.resize((width, int(img.height * sc)))
    d = ImageDraw.Draw(img)
    for label, (x, y, w, h) in boxes:
        x, y, w, h = [int(v * sc) for v in (x, y, w, h)]
        d.rectangle([x, y, x + w, y + h], outline=(0, 255, 200), width=3)
        d.rectangle([x, max(0, y - 16), x + 8 * len(label) + 8, y], fill=(0, 255, 200))
        d.text((x + 4, max(0, y - 15)), label, fill=(0, 0, 0))
    img.save(path, "PNG")


def analyze_video(path, out_dir, key, sample_interval_s=None):
    info = probe(path)
    fmt = info.get("format", {}) or {}
    vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    tags = dict(fmt.get("tags", {}) or {})
    tags.update({k: v for k, v in (vstream.get("tags", {}) or {}).items() if k not in tags})
    meta = {"duration_s": round(float(fmt.get("duration", 0) or 0), 2), "codec": vstream.get("codec_name"),
            "width": vstream.get("width"), "height": vstream.get("height"), "has_audio": has_audio,
            "creation_time": tags.get("creation_time"), "encoder": tags.get("encoder"), "container_tags": tags}

    # spread the spatial samples over the whole clip (previously only the first 20 s were sampled)
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if not math.isfinite(fps) or fps <= 0:
        fps = 25.0
    interval = float(sample_interval_s if sample_interval_s is not None else os.environ.get("TRUTHLENS_VIDEO_SAMPLE_INTERVAL", SAMPLE_EVERY_S))
    if not math.isfinite(interval) or interval < 0.1 or interval > 60:
        cap.release()
        raise ValueError("Video sample interval must be between 0.1 and 60 seconds")
    analysed = min(meta["duration_s"] or 0, MAX_FRAMES / fps)
    every = max(interval, analysed / MAX_SAMPLES) if analysed else interval
    meta["sample_interval_s"] = round(every, 2)
    meta["requested_sample_interval_s"] = interval
    meta["spatial_max_side"] = MAX_SPATIAL_SIDE
    meta["fps"] = round(fps, 2)
    diffs, times = [], []
    prev = None
    samples = []          # (time, rgb)
    next_sample = 0.0
    i = 0
    frames_cache = {}     # keep a few small frames around cuts for thumbnails
    started = time.monotonic()
    budget_exceeded = False
    while i < MAX_FRAMES:
        if time.monotonic() - started > 90:
            budget_exceeded = True
            break
        ok, bgr = cap.read()
        if not ok:
            break
        position = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        t = position if math.isfinite(position) and position >= 0 and (not times or position > times[-1]) else i / fps
        small = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (160, 90)).astype(np.float32)
        if prev is not None:
            diffs.append(float(np.abs(small - prev).mean()))
            times.append(t)
        prev = small
        frames_cache[i] = cv2.resize(bgr, (320, int(320 * bgr.shape[0] / bgr.shape[1])))
        frames_cache.pop(i - 3, None)
        if t >= next_sample and len(samples) < MAX_SAMPLES:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            scale = min(1.0, MAX_SPATIAL_SIDE / max(rgb.shape[:2]))
            if scale < 1:
                rgb = cv2.resize(rgb, (max(16, int(rgb.shape[1] * scale)), max(16, int(rgb.shape[0] * scale))), interpolation=cv2.INTER_AREA)
            samples.append((t, rgb))
            next_sample = t + every
        # stash the frame pair for any big jump (detected after the loop)
        if len(diffs) > 0:
            frames_cache.setdefault("pairs", {})[round(t, 3)] = (frames_cache.get(i - 1), frames_cache[i])
            if len(frames_cache["pairs"]) > 400:
                frames_cache["pairs"].pop(next(iter(frames_cache["pairs"])))
        i += 1
    cap.release()
    meta["frames_read"] = i
    limitations = ["Frames are checked for local noise inconsistencies only; a high-quality, fully generated video can be internally consistent.",
                   "Re-encoding by social platforms smooths noise and can hide or create weak signals.",
                   "Hard cuts are normal in edited news or vlogs; they only matter when footage is claimed to be one continuous take."]
    if i >= MAX_FRAMES:
        limitations.append(f"Only the first {i / fps:.0f} s ({i} frames) of {meta['duration_s']:.0f} s were analysed (prototype limit).")
    limitations.append(f"Spatial checks ran on one frame every {every:.1f} s; frame-to-frame cut detection ran on every decoded frame.")
    findings, artifacts = [], {}
    if budget_exceeded:
        limitations.append("Video decoding stopped at the 90-second analysis budget; only the recorded timestamps were assessed.")
    limitations.append(f"Spatial analysis copies are limited to {MAX_SPATIAL_SIDE}px on the longest side. The original video and playback resolution are preserved.")
    if i < 3:
        raise MediaError("Could not decode enough video frames for analysis. Original evidence is preserved; try a compatible codec.", 422)

    # ---- temporal discontinuities
    d = np.array(diffs)
    z = robust_z(d)
    cuts = [k for k in range(len(d)) if z[k] > 8 and d[k] > 8]
    # merge cuts within 3 frames
    merged = []
    for k in cuts:
        if not merged or k - merged[-1] > 3:
            merged.append(k)
    meta["timeline"] = [{"t": round(times[k], 2), "diff": round(diffs[k], 2)} for k in range(0, len(d), max(1, len(d) // 300))]
    cut_times = [round(times[k], 2) for k in merged]
    meta["cuts"] = cut_times
    pairs = frames_cache.get("pairs", {})
    produced = len(merged) > 3
    meta["produced_video"] = produced
    if produced:
        # Many hard cuts = edited / produced footage (trailer, news package, vlog). Cuts are normal there;
        # they only become relevant if someone claims the footage is one continuous / live take (see correlation).
        findings.append(finding("video", "edited-structure", f"Edited video with {len(merged)} hard cuts",
                                f"Hard cuts at {', '.join(f'{c:.1f}s' for c in cut_times[:12])}{' ...' if len(cut_times) > 12 else ''}. "
                                "Multiple cuts are normal for produced content and are not evidence of manipulation on their own. "
                                "They matter only if the footage is presented as one continuous or live recording.",
                                0.1, {"type": "file"}, ["Normal editing of a produced video"]))
    for n, k in enumerate([] if produced else merged):
        t = times[k]
        art = f"{key}_cut{n}"
        pair = pairs.get(round(t, 3))
        if pair and pair[0] is not None:
            both = np.concatenate([pair[0], np.full((pair[0].shape[0], 6, 3), 255, np.uint8), pair[1]], axis=1)
            cv2.imwrite(os.path.join(out_dir, art + ".png"), both)
            artifacts[art] = art + ".png"
        findings.append(finding("video", "temporal-discontinuity", "Abrupt cut / frame discontinuity",
                                f"At {t:.2f}s the picture changes {d[k]:.0f} grey levels between consecutive frames, about {d[k] / (np.median(d) + 1e-6):.0f}x the typical frame-to-frame change. "
                                "This is a hard cut: frames before and after do not come from one continuous shot.",
                                0.45, {"type": "frame", "time": round(t, 2)},
                                ["Normal editing cut between camera angles", "Dropped frames during recording or streaming"],
                                artifact=art if art in artifacts else None))
    for a, b in ([] if produced else zip(cut_times, cut_times[1:])):
        if b - a <= 15:
            findings.append(finding("video", "temporal-discontinuity", "Possible inserted segment",
                                    f"Two cuts {b - a:.1f}s apart ({a:.2f}s and {b:.2f}s): the footage between them may come from a different shot or recording.",
                                    0.6, {"type": "segment", "start": a, "end": b},
                                    ["Cutaway shot inserted by an editor (normal in news packages)"]))

    # ---- spatial: persistent anomalous region across sampled frames
    tracks = []    # each: {"bbox", "hits": [(t, region)], }
    frame_z = {}   # sampled time -> strongest local noise deficit (robust z, 0 = none) measured in that frame
    levels = []
    for t, rgb in samples[:: max(1, len(samples) // 10)]:
        std, valid = noise_map(rgb)
        levels.append(float(np.median(std[valid])) if valid.any() else 0.0)
    meta["noise_level"] = round(float(np.median(levels)), 2) if levels else None
    if levels and np.median(levels) < 1.0:
        # Animation, screen recording or heavily denoised footage: no sensor noise to compare regions against.
        limitations.append(f"Frames carry almost no sensor noise (median {np.median(levels):.2f}); region checks were skipped. "
                           "Typical of animation, screen recordings or heavily denoised video.")
    for t, rgb in ([] if levels and np.median(levels) < 1.0 else samples):
        std, valid = noise_map(rgb)
        if not valid.any() or float(np.median(std[valid])) < 1.0:
            continue          # this frame has no sensor noise to compare against (flat graphic, fade, animation)
        shot = sum(1 for c in cut_times if c <= t)   # a region is only tracked within one continuous shot
        regions, _ = find_anomalous_regions(rgb, use_ela=False)
        sm = [r["noise_z"] for r in regions if r["kind"] == "smooth" and r["size_frac"] <= 0.4]
        frame_z[round(t, 2)] = round(min(sm), 1) if sm else 0.0
        for r in regions:
            if r["kind"] != "smooth" or r["size_frac"] > 0.4:
                continue
            for tr in tracks:
                if tr["shot"] == shot and _iou(tr["bbox"], r["bbox"]) > 0.2:
                    tr["hits"].append((t, r))
                    tr["bbox"] = r["bbox"]
                    break
            else:
                tracks.append({"bbox": r["bbox"], "hits": [(t, r)], "shot": shot})
    n_samples = len(samples)
    for tr in sorted(tracks, key=lambda x: -len(x["hits"]))[:2]:
        frac = len(tr["hits"]) / n_samples
        if len(tr["hits"]) < 3 or frac < 0.25:
            continue
        ratio = float(np.median([h[1]["noise_ratio"] for h in tr["hits"]]))
        t_best, r_best = min(tr["hits"], key=lambda h: h[1]["noise_z"])
        rgb = next(s[1] for s in samples if s[0] == t_best)
        sx = (meta.get("width") or rgb.shape[1]) / rgb.shape[1]
        sy = (meta.get("height") or rgb.shape[0]) / rgb.shape[0]
        mapped_bbox = [int(r_best["bbox"][j] * (sx if j % 2 == 0 else sy)) for j in range(4)]
        f = finding("video", "face-or-region-manipulation", "Region lacking sensor noise across many frames",
                    f"In {len(tr['hits'])} of {n_samples} sampled frames ({frac:.0%}) the same area has only about {ratio:.0%} of the noise level of the rest of the frame. "
                    "This is a persistent local noise anomaly. It can be consistent with editing, blur or denoising; it does not confirm an AI-generated region.",
                    0.4 + 0.45 * min(frac / 0.6, 1.0),
                    {"type": "frame", "time": round(t_best, 2), "bbox": mapped_bbox,
                     "times": [round(h[0], 2) for h in tr["hits"]]},
                    ["Shallow depth of field (blurred face or background)", "Beauty filter or noise reduction applied by the phone",
                     "Strong platform compression on low-detail areas"])
        art = f"{key}_region_{f['id']}"
        _thumb_with_box(rgb, [(f["id"], r_best["bbox"])], os.path.join(out_dir, art + ".png"))
        artifacts[art] = art + ".png"
        f["artifact"] = art
        findings.append(f)

    # per-frame record for the frame-by-frame view: evenly spaced samples plus every flagged frame,
    # each with its measured local-noise deficit and the findings that refer to it
    flagged_at = {}
    for f in findings:
        loc = f["location"]
        for t in loc.get("times", []) + ([loc["time"]] if loc.get("type") == "frame" else []):
            flagged_at.setdefault(round(t, 2), set()).add(f["id"])
        if loc.get("type") == "segment":
            for t0, _ in samples:
                if loc["start"] <= t0 <= loc["end"]:
                    flagged_at.setdefault(round(t0, 2), set()).add(f["id"])
    frames = []
    for t, rgb in samples:
        tt = round(t, 2)
        near_cut = [c for c in cut_times if abs(c - t) <= every / 2]
        name = f"{key}_frame_{int(round(t * 100)):06d}.jpg"
        Image.fromarray(cv2.resize(rgb, (240, int(240 * rgb.shape[0] / rgb.shape[1])))).save(os.path.join(out_dir, name), quality=80)
        frames.append({"t": tt, "thumb": name, "noise_deficit_z": frame_z.get(tt), "near_cut": near_cut[0] if near_cut else None,
                       "findings": sorted(flagged_at.get(tt, set())), "spatial_checked": tt in frame_z,
                       "analysis_width": rgb.shape[1], "analysis_height": rgb.shape[0]})
    meta["frames"] = frames[:60]
    # Temporal groups are candidate review intervals, not confirmed manipulations.
    intervals = []
    for frame in frames:
        if not frame["findings"]:
            continue
        if intervals and frame["t"] - intervals[-1]["last_sample"] <= every * 1.5:
            interval = intervals[-1]
            interval["end"] = min(meta["duration_s"] or frame["t"], round(frame["t"] + every / 2, 2))
            interval["last_sample"] = frame["t"]
            interval["finding_refs"] = sorted(set(interval["finding_refs"] + frame["findings"]))
        else:
            intervals.append({"start": max(0, round(frame["t"] - every / 2, 2)),
                              "end": min(meta["duration_s"] or frame["t"], round(frame["t"] + every / 2, 2)),
                              "last_sample": frame["t"], "finding_refs": list(frame["findings"]),
                              "status": "candidate_interval"})
    meta["candidate_intervals"] = [{k: v for k, v in interval.items() if k != "last_sample"} for interval in intervals]

    # keyframe contact sheet for the report
    if samples:
        picks = samples[:: max(1, len(samples) // 8)][:8]
        thumbs = [cv2.resize(s[1], (200, int(200 * s[1].shape[0] / s[1].shape[1]))) for s in picks]
        sheet = np.concatenate(thumbs, axis=1)
        Image.fromarray(sheet).save(os.path.join(out_dir, f"{key}_frames.png"))
        artifacts[f"{key}_frames"] = f"{key}_frames.png"
        meta["contact_sheet_times"] = [round(s[0], 2) for s in picks]
    meta["frames_sampled"] = n_samples
    return {"modality": "video", "metadata": meta, "findings": findings, "artifacts": artifacts, "limitations": limitations,
            "analysis_status": "partial" if budget_exceeded else "complete"}


def extract_audio_track(path, out_path):
    r = run_media("ffmpeg", ["-y", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", out_path], timeout=60)
    return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000
