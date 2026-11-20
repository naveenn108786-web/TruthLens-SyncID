"""People consistency analysis (EXPERIMENTAL).

Looks for a person who was pasted into (or regenerated inside) a group photo by comparing the
people in the same image with each other. It does NOT judge anyone's appearance and never decides
that a person is "AI-generated"; it only reports which person's image statistics differ from the
rest of the group, by how much, and which innocent causes could explain that.

What it detects: faces, with the offline YuNet face detector (libfacedetection, BSD-3 licence,
bundled ONNX file, run by OpenCV). The head-and-shoulders region of each person is ESTIMATED from
the face box; it is not a body outline or a segmentation mask.

Measurements per person (all computed on pixels, relative to the other people in the same photo):
  noise       sensor-noise level in the face (Immerkaer noise estimate on flat, unclipped pixels)
  sharpness   fine detail in the face (Laplacian variance at a fixed face size)
  blockiness  JPEG 8x8 block-edge strength around the head (compression history)
  brightness  mean lightness of the face (L*)
  colour      colour cast of the face (a*/b* distance from the group median)
  boundary    noise step across the estimated head outline (inside vs just outside)

A person is highlighted only when at least 3 people are compared and the person is an outlier
(robust z >= 3) on at least 2 measurements, one of which is noise, sharpness, blockiness or
boundary. Brightness and colour depend on skin tone, clothing and light position, so they never
highlight anyone on their own.

The result is kept apart from the manipulation score: it adds no finding and changes no score.
"""
import os

import cv2
import numpy as np

MODEL = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_n.onnx")
DETECT_SIDE = 1600      # detection runs on a copy downscaled to this size
MIN_FACE = 28           # px (original image): smaller faces are too small to measure reliably
MIN_PEOPLE = 3          # fewer people cannot be compared with a group
Z_FLAG = 3.0            # robust z needed for a measurement to count as an outlier
ACQ = ("noise", "sharpness", "blockiness", "boundary")   # appearance-neutral measurements
LABELS = {"noise": "sensor noise", "sharpness": "sharpness", "blockiness": "JPEG blockiness",
          "brightness": "brightness", "colour": "colour cast", "boundary": "noise step at the head outline"}
FALSE_POSITIVES = [
    "Distance from the camera and focus: people nearer or further than the focus plane are softer and can look less noisy.",
    "Motion blur: a person who moved during the exposure is less sharp than the others.",
    "Uneven light: flash, a lamp or shadow changes brightness, colour and the visible noise level of one person.",
    "Phone portrait or beauty modes smooth faces selectively.",
    "Make-up, glasses, beards, hair over the face or a turned head change texture measurements.",
    "Recompression (e.g. WhatsApp) changes noise and block patterns across the whole image and weakens every measurement.",
    "Skin tone legitimately differs between people; brightness and colour are therefore never used on their own.",
]
LIMITATIONS = [
    "Detects faces only; head-and-shoulders regions are estimated from the face box, not traced body outlines.",
    "People whose face is hidden, turned away, very small or cut off are not detected and not compared.",
    "Needs at least 3 detected people; with fewer, measurements are shown but nobody is highlighted.",
    "A removed person cannot be detected by finding people; removal can only show up as a smoothed or inpainted area, which the main noise check reports as a region finding.",
    "A generated person whose noise, sharpness and compression match the rest of the photo will not stand out.",
    "Thresholds are hand-set and were checked only with controlled sensitivity tests (blur, brightness, recompression, pasted faces), not on a labelled set of AI-edited photos.",
]

_detector = None


def _get_detector():
    global _detector
    if _detector is None:
        if not os.path.exists(MODEL):
            raise FileNotFoundError(f"face detector model missing: {MODEL}")
        _detector = cv2.FaceDetectorYN.create(MODEL, "", (320, 320), 0.7, 0.3, 200)
    return _detector


def detect_faces(rgb):
    """Faces as [(x, y, w, h, confidence)] in the coordinates of rgb."""
    h, w = rgb.shape[:2]
    s = min(1.0, DETECT_SIDE / max(h, w))
    small = cv2.resize(rgb, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else rgb
    det = _get_detector()
    det.setInputSize((small.shape[1], small.shape[0]))
    _, faces = det.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    out = []
    for f in (faces if faces is not None else []):
        x, y, fw, fh = (float(v) / s for v in f[:4])
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(w, int(round(x + fw))), min(h, int(round(y + fh)))
        if min(x1 - x0, y1 - y0) >= MIN_FACE:
            out.append((x0, y0, x1 - x0, y1 - y0, round(float(f[-1]), 3)))
    return sorted(out, key=lambda b: b[0])


def _clip(x0, y0, x1, y1, w, h):
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def head_box(face, w, h):
    x, y, fw, fh = face[:4]
    return _clip(x - 0.3 * fw, y - 0.45 * fh, x + 1.3 * fw, y + 1.15 * fh, w, h)


def shoulders_box(face, w, h):
    x, y, fw, fh = face[:4]
    return _clip(x - 1.0 * fw, y - 0.5 * fh, x + 2.0 * fw, y + 2.8 * fh, w, h)


_IMM = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)


def _noise_map(gray):
    """Per-pixel |Immerkaer noise operator| (a continuous noise estimate) and a mask of usable pixels:
    not clipped and not on strong edges, where texture would be mistaken for noise."""
    r = np.abs(cv2.filter2D(gray, cv2.CV_32F, _IMM)) * np.sqrt(np.pi / 2) / 6
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    mag = np.hypot(gx, gy)
    ok = (gray > 8) & (gray < 247)
    return r, mag, ok


def _flat(mag, sel):
    """Usable pixels in sel that are not on the strongest 30% of edges inside sel."""
    if sel.sum() < 50:
        return sel
    return sel & (mag <= np.percentile(mag[sel], 70))


def _noise(gray_patch):
    """Mean noise estimate over the flat, unclipped part of the patch."""
    r, mag, ok = _noise_map(gray_patch)
    m = _flat(mag, ok)
    return float(r[m].mean()) if m.sum() > 50 else float("nan")


def _sharpness(gray_patch):
    g = cv2.resize(gray_patch, (64, 64), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def _blockiness(gray, x0, y0, x1, y1):
    """Strength of luminance steps on the image's 8x8 JPEG grid vs between grid lines (1.0 = no grid)."""
    p = gray[y0:y1, x0:x1]
    if p.shape[0] < 24 or p.shape[1] < 24:
        return float("nan")
    dx = np.abs(np.diff(p, axis=1)).mean(axis=0)           # step between column c and c+1
    dy = np.abs(np.diff(p, axis=0)).mean(axis=1)
    cx = (np.arange(x0, x0 + dx.size) % 8) == 7               # boundary between ...7 and ...8 on the global grid
    cy = (np.arange(y0, y0 + dy.size) % 8) == 7
    on = np.concatenate([dx[cx], dy[cy]]).mean()
    off = np.concatenate([dx[~cx], dy[~cy]]).mean() + 1e-6
    return float(on / off)


def _boundary(gray, hb, w, h):
    """Noise just inside the head box edge vs just outside it (log ratio; 0 = same)."""
    x0, y0, x1, y1 = hb
    band = max(4, int(0.12 * (x1 - x0)))
    inner = np.zeros(gray.shape, bool)
    inner[y0:y1, x0:x1] = True
    core = np.zeros(gray.shape, bool)
    core[min(y1, y0 + band):max(y0, y1 - band), min(x1, x0 + band):max(x0, x1 - band)] = True
    outer = np.zeros(gray.shape, bool)
    ox0, oy0, ox1, oy1 = _clip(x0 - band, y0 - band, x1 + band, y1 + band, w, h)
    outer[oy0:oy1, ox0:ox1] = True
    ring_in, ring_out = inner & ~core, outer & ~inner
    r, mag, ok = _noise_map(gray)
    a, b = _flat(mag, ring_in & ok), _flat(mag, ring_out & ok)
    if a.sum() < 50 or b.sum() < 50:
        return float("nan")
    return float(np.log((r[a].mean() + 0.2) / (r[b].mean() + 0.2)))


def _robust_z(vals, floor):
    v = np.asarray(vals, float)
    ok = np.isfinite(v)
    z = np.full(v.shape, np.nan)
    if ok.sum() < MIN_PEOPLE:
        return z
    med = np.median(v[ok])
    mad = np.median(np.abs(v[ok] - med)) * 1.4826
    z[ok] = (v[ok] - med) / max(mad, floor)
    return z


def _reason(m, z, val, med):
    if m == "noise":
        return f"sensor noise {val / med:.0%} of the group median" if med > 0 else "sensor noise differs from the group"
    if m == "sharpness":
        return f"{val / med:.1f}x the group's median sharpness" if med > 0 else "sharpness differs from the group"
    if m == "blockiness":
        return f"JPEG block pattern {'stronger' if z > 0 else 'weaker'} than around the other people ({val:.2f} vs median {med:.2f})"
    if m == "boundary":
        return f"noise {'jumps up' if z > 0 else 'drops'} across the head outline more than for the others"
    if m == "brightness":
        return f"face lightness {val:.0f} vs group median {med:.0f} (L*, 0-100)"
    return f"colour cast {val:.1f} units from the group median"


def analyze_people(rgb, out_dir=None, key=None):
    """rgb: full-resolution image array. Returns the experimental people block for the report."""
    h, w = rgb.shape[:2]
    faces = detect_faces(rgb)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    people, raw = [], {m: [] for m in LABELS}
    for i, f in enumerate(faces):
        x, y, fw, fh, conf = f
        # inner face (avoid hair and background at the box edge)
        ix0, iy0, ix1, iy1 = _clip(x + 0.15 * fw, y + 0.15 * fh, x + 0.85 * fw, y + 0.9 * fh, w, h)
        fg = gray[iy0:iy1, ix0:ix1]
        flab = lab[iy0:iy1, ix0:ix1].reshape(-1, 3)
        hb, sb = head_box(f, w, h), shoulders_box(f, w, h)
        vals = {"noise": _noise(fg), "sharpness": _sharpness(fg), "blockiness": _blockiness(gray, *hb),
                "brightness": float(flab[:, 0].mean() * 100 / 255), "colour_ab": (float(flab[:, 1].mean() - 128), float(flab[:, 2].mean() - 128)),
                "boundary": _boundary(gray, hb, w, h)}
        people.append({"id": f"P{i + 1}", "face_bbox": [x, y, fw, fh], "region_bbox": [sb[0], sb[1], sb[2] - sb[0], sb[3] - sb[1]],
                       "confidence": conf, "_v": vals})
    # colour: distance of each face's a*/b* from the group median
    if people:
        ab = np.array([p["_v"]["colour_ab"] for p in people])
        med_ab = np.median(ab, axis=0)
        for p, d in zip(people, np.linalg.norm(ab - med_ab, axis=1)):
            p["_v"]["colour"] = float(d)
    compared = len(people) >= MIN_PEOPLE
    # ratio-type measures are compared on a log scale; floors stop a very uniform group from inflating z
    series = {
        "noise": ([np.log(p["_v"]["noise"] + 0.1) for p in people], 0.12),
        "sharpness": ([np.log(p["_v"]["sharpness"] + 1) for p in people], 0.15),
        "blockiness": ([np.log(p["_v"]["blockiness"]) if p["_v"]["blockiness"] > 0 else np.nan for p in people], 0.04),
        "boundary": ([p["_v"]["boundary"] for p in people], 0.08),
        "brightness": ([p["_v"]["brightness"] for p in people], 4.0),
        "colour": ([p["_v"]["colour"] for p in people], 2.0),
    }
    Z = {m: _robust_z(v, fl) for m, (v, fl) in series.items()}
    meds = {m: float(np.nanmedian([p["_v"][m] for p in people])) if people else float("nan") for m in LABELS}
    for i, p in enumerate(people):
        v = p.pop("_v")
        p["measures"] = {m: (round(v[m], 3) if np.isfinite(v[m]) else None) for m in LABELS}
        p["z"] = {m: (round(float(Z[m][i]), 2) if np.isfinite(Z[m][i]) else None) for m in LABELS}
        out =[m for m in LABELS if compared and p["z"][m] is not None and abs(p["z"][m]) >= Z_FLAG
               and not (m == "colour" and p["z"][m] < 0)]
        p["outliers"] = out
        p["flagged"] = compared and len(out) >= 2 and any(m in ACQ for m in out)
        p["reasons"] = [f"{LABELS[m]}: {_reason(m, p['z'][m], v[m], meds[m])} (robust z {p['z'][m]:+.1f})" for m in out]
    flagged = [p["id"] for p in people if p["flagged"]]
    if not people:
        summary = "No faces were detected, so no people were compared."
    elif not compared:
        summary = f"{len(people)} face(s) detected; at least {MIN_PEOPLE} are needed to compare people, so nobody is highlighted."
    elif flagged:
        summary = (f"{len(people)} people compared. {', '.join(flagged)} differ{'s' if len(flagged) == 1 else ''} from the group on two or more measurements; "
                   "review the listed reasons and innocent causes. This is not a finding that the person was inserted or generated.")
    else:
        summary = f"{len(people)} people compared; none differs from the group on two or more measurements. This does not prove nobody was edited."
    block = {"experimental": True, "affects_score": False,
             "detector": "YuNet face detector (libfacedetection, BSD-3), offline via OpenCV",
             "detects": "Faces. Head-and-shoulders regions are estimated from each face box, not traced body outlines.",
             "count": len(people), "compared": compared, "flagged": flagged, "summary": summary,
             "thresholds": {"min_people": MIN_PEOPLE, "z": Z_FLAG, "min_outlier_measures": 2},
             "people": people, "false_positives": FALSE_POSITIVES, "limitations": LIMITATIONS}
    if out_dir and key and people:
        name = f"{key}_people.png"
        _draw(rgb, people, os.path.join(out_dir, name))
        block["artifact"] = name
    return block


def _draw(rgb, people, path):
    im = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    t = max(2, int(max(im.shape[:2]) / 600))
    for p in people:
        x, y, w, h = p["region_bbox"]
        col = (60, 80, 255) if p["flagged"] else (200, 190, 40)
        cv2.rectangle(im, (x, y), (x + w, y + h), col, t)
        fx, fy, fw, fh = p["face_bbox"]
        cv2.rectangle(im, (fx, fy), (fx + fw, fy + fh), col, max(1, t // 2))
        cv2.putText(im, p["id"], (x + 4, y + 18 * t // 2 + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * t / 2 + 0.3, col, t)
    cv2.imwrite(path, im)
