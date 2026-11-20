"""Controlled sensitivity test for the experimental people-consistency analysis.

Usage:  python samples/eval_people.py GROUP_PHOTO.jpg [DONOR_FACE_PHOTO.jpg]

For every detected person in a group photo, applies one controlled edit to that person's head region
(blur, smoothing, brightening, recompression with a shifted JPEG grid, resampling, or a face pasted in
from another photo), re-runs the analysis on the edited photo, and counts whether the edited person was
highlighted and whether anyone else was. Raw counts only.

These are SENSITIVITY tests of the measurements. They do not show that the analysis can detect an
AI-inserted person: real generative edits are not the same as these controlled edits.
"""
import io
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from truthlens.forensics.persons import analyze_people, detect_faces, head_box  # noqa: E402


def jpeg(rgb, q):
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "JPEG", quality=q)
    return np.asarray(Image.open(io.BytesIO(b.getvalue())).convert("RGB"))


def edit(rgb, box, kind, donor=None):
    x0, y0, x1, y1 = box
    out = rgb.copy()
    reg = out[y0:y1, x0:x1]
    if kind == "blur":
        new = cv2.GaussianBlur(reg, (0, 0), 2.0)
    elif kind == "smooth":                               # strong edge-preserving smoothing (beauty filter / generated skin)
        new = cv2.bilateralFilter(reg, 9, 40, 9)
    elif kind == "brighten":
        new = np.clip(reg.astype(np.int16) + 30, 0, 255).astype(np.uint8)
    elif kind == "recompress-shifted":                   # region saved separately at low quality, pasted 3 px off the 8x8 grid
        pad = np.pad(reg, ((3, 0), (3, 0), (0, 0)), mode="edge")
        new = jpeg(pad, 35)[3:, 3:]
    elif kind == "resample":                             # downscale and upscale (typical of regenerated patches)
        h, w = reg.shape[:2]
        new = cv2.resize(cv2.resize(reg, (max(8, w // 3), max(8, h // 3)), interpolation=cv2.INTER_AREA), (w, h), interpolation=cv2.INTER_CUBIC)
    elif kind == "paste-donor":                          # a face from another photo, resized into the head box
        new = cv2.resize(donor, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    else:
        raise ValueError(kind)
    out[y0:y1, x0:x1] = new
    return jpeg(out, 90)                                 # the edited photo is saved again, as an editor would


def match(people, box):
    """Which detected person corresponds to the edited head box (largest overlap), if any."""
    best, pid = 0, None
    for p in people:
        x, y, w, h = p["face_bbox"]
        ov = max(0, min(x + w, box[2]) - max(x, box[0])) * max(0, min(y + h, box[3]) - max(y, box[1]))
        if ov > best:
            best, pid = ov, p["id"]
    return pid


def main(path, donor_path=None):
    rgb = jpeg(np.asarray(Image.open(path).convert("RGB")), 90)
    H, W = rgb.shape[:2]
    base = analyze_people(rgb)
    print(f"{os.path.basename(path)}: {base['count']} people; unedited photo flags {base['flagged'] or 'nobody'}")
    faces = detect_faces(rgb)
    kinds = ["blur", "smooth", "brighten", "recompress-shifted", "resample"]
    donor = None
    if donor_path:
        d = np.asarray(Image.open(donor_path).convert("RGB"))
        df = detect_faces(d)
        if df:
            x0, y0, x1, y1 = head_box(df[0], d.shape[1], d.shape[0])
            donor = d[y0:y1, x0:x1]
            kinds.append("paste-donor")
    print(f"{'edit':20} {'edited person highlighted':>26} {'edited person not found':>24} {'others highlighted (false alarms)':>34}")
    total = {"hit": 0, "n": 0, "fa": 0, "others": 0}
    for k in kinds:
        hit = lost = fa = others = 0
        for f in faces:
            box = head_box(f, W, H)
            res = analyze_people(edit(rgb, box, k, donor))
            pid = match(res["people"], box)
            if pid is None:
                lost += 1
            hit += pid in res["flagged"]
            fa += len([x for x in res["flagged"] if x != pid])
            others += max(0, res["count"] - 1)
        print(f"{k:20} {hit:>20} of {len(faces):<3} {lost:>20} {fa:>22} of {others}")
        total["hit"] += hit; total["n"] += len(faces); total["fa"] += fa; total["others"] += others
    print(f"{'all edits':20} {total['hit']:>20} of {total['n']:<3} {'':>20} {total['fa']:>22} of {total['others']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
