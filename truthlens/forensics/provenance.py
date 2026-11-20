"""Authenticity & Provenance engine (plus the reference-based parts of manipulation detection).

For an uploaded image it answers "where did this come from and what happened to it?":
1. SHA-256 against the registry: exact, byte-for-byte match to a registered original?
2. Perceptual hash: visually similar to a registered original (re-encoded, resized or edited copy)?
3. Visual difference vs that original: which areas changed?
4. Camera (sensor-noise) consistency vs that original: does the fine noise pattern still match?
   Same capture => residual noise correlates strongly. Edited areas lose the correlation.
5. Metadata vs the original: capture date / device / software changes.
6. C2PA content credentials: signed? intact? who signed? declared AI-generated?
7. File-hash history: has this exact file been examined in an earlier case?

Hard rule: being unregistered, having no metadata or having no C2PA credentials is NEVER
treated as evidence of manipulation. Those results are reported as neutral information (score 0).
"""
import json
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .common import finding
from .c2pa_check import check_c2pa
from .. import registry

NOT_EVIDENCE = "This alone is not evidence of manipulation."


def _rgb(path, size=None):
    img = Image.open(path).convert("RGB")
    if size and img.size != size:
        img = img.resize(size, Image.LANCZOS)
    return np.asarray(img)


def visual_diff(ref_path, q_path):
    """Changed regions between registered original and query (query resized to original size)."""
    ref_img = Image.open(ref_path).convert("RGB")
    ref = np.asarray(ref_img.filter(ImageFilter.GaussianBlur(2)), np.int16)
    q = np.asarray(Image.open(q_path).convert("RGB").resize(ref_img.size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(2)), np.int16)
    d = np.abs(ref - q).mean(axis=2).astype(np.float32)
    mask = (d > 8).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = [list(map(int, stats[i][:4])) for i in range(1, n) if stats[i][4] > 150]
    boxes.sort(key=lambda b: -b[2] * b[3])
    return {"changed_fraction": float((d > 8).mean()), "boxes": boxes[:5], "diff": d}


def noise_correlation(ref_path, q_path, block=32):
    """Block-wise correlation of the high-frequency noise residual of query vs original."""
    ref_img = Image.open(ref_path)
    q_img = Image.open(q_path)
    if ref_img.size != q_img.size:
        return None   # resized copies break pixel alignment: not comparable
    def res(img):
        g = np.asarray(img.convert("L"), np.float32)
        return g - cv2.GaussianBlur(g, (5, 5), 0)
    a, b = res(ref_img), res(q_img)
    h, w = a.shape
    hb, wb = h // block, w // block
    A = a[:hb * block, :wb * block].reshape(hb, block, wb, block).transpose(0, 2, 1, 3).reshape(hb, wb, -1)
    B = b[:hb * block, :wb * block].reshape(hb, block, wb, block).transpose(0, 2, 1, 3).reshape(hb, wb, -1)
    A = A - A.mean(-1, keepdims=True)
    B = B - B.mean(-1, keepdims=True)
    corr = (A * B).sum(-1) / (np.sqrt((A ** 2).sum(-1) * (B ** 2).sum(-1)) + 1e-6)
    return {"grid": corr, "block": block, "median": float(np.median(corr))}


def _comparison_png(ref_path, q_path, boxes, corr, path, width=360):
    ref = Image.open(ref_path).convert("RGB")
    q = Image.open(q_path).convert("RGB").resize(ref.size)
    sc = width / ref.width
    h = int(ref.height * sc)
    panels = [ref.resize((width, h)), q.resize((width, h))]
    # third panel: query with changed regions and noise-mismatch blocks
    third = q.resize((width, h)).copy()
    ov = Image.new("RGBA", third.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    if corr is not None:
        bs = corr["block"] * sc
        for (y, x), c in np.ndenumerate(corr["grid"]):
            if c < min(0.6, corr["median"] - 0.3):
                d.rectangle([x * bs, y * bs, (x + 1) * bs, (y + 1) * bs], fill=(255, 60, 60, 110))
    third = Image.alpha_composite(third.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(third)
    for x, y, bw, bh in boxes:
        d.rectangle([x * sc, y * sc, (x + bw) * sc, (y + bh) * sc], outline=(0, 255, 200), width=3)
    panels.append(third)
    out = Image.new("RGB", (width * 3 + 12, h + 22), (13, 17, 23))
    dd = ImageDraw.Draw(out)
    for i, (p, label) in enumerate(zip(panels, ["Registered original", "Uploaded file", "Changed areas / noise mismatch"])):
        out.paste(p, (i * (width + 6), 22))
        dd.text((i * (width + 6) + 4, 5), label, fill=(230, 237, 243))
    out.save(path)


def previous_cases(sha, current_investigation):
    """Earlier investigations (from the database) that contained a file with the same SHA-256."""
    from .. import db
    hits = [{"case_id": r["case_ref"], "title": r["title"], "date": r["case_created"], "as": r["filename"], "score": r["indicator"]}
            for r in db.evidence_by_sha(sha, current_investigation)]
    return hits[-5:]


def analyze_provenance(path, sha, meta, out_dir, key, investigation_id, cases_dir=None, is_image=True):
    findings, artifacts = [], {}
    prov = {"sha256": sha}

    # ---------- 7. file-hash history
    hist = previous_cases(sha, investigation_id)
    prov["history"] = hist
    if hist:
        findings.append(finding("provenance", "history", "Seen in earlier investigations",
                                f"This exact file (same SHA-256) was examined before in {len(hist)} case(s), most recently '{hist[-1]['title']}' on {hist[-1]['date'][:10]}.",
                                0.0, {"type": "file"}))

    # ---------- 1/2. registry matching
    look = registry.lookup(path, sha) if is_image else {"exact": next((e for e in registry.load() if e["sha256"] == sha), None), "similar": [], "phash": None}
    prov["phash"] = look.get("phash")
    ex = look["exact"]
    best = look["similar"][0] if look["similar"] else None
    prov["registry"] = {"exact": ex and {k: ex.get(k) for k in ("id", "filename", "source", "registered_at")},
                        "similar": [{"id": e["id"], "filename": e["filename"], "distance": d, "source": e.get("source")} for e, d in look["similar"][:3]]}
    if ex:
        prov["camera_note"] = "Identical to the registered original, so no sensor-noise comparison is needed."
        findings.append(finding("provenance", "registry-match", "Exact match to registered original",
                                f"SHA-256 is identical to registered original {ex['id']} ({ex['filename']}, source: {ex.get('source') or 'n/a'}, registered {ex['registered_at'][:10]}). "
                                "The file is byte-for-byte the same as the registered version.",
                                0.0, {"type": "file"}))
    elif best:
        ref, dist = best
        rp = registry.path_of(ref)
        dif = visual_diff(rp, path)
        cor = noise_correlation(rp, path)
        prov["reference"] = {"id": ref["id"], "filename": ref["filename"], "distance": dist}
        prov["diff"] = {"changed_fraction": round(dif["changed_fraction"], 4), "boxes": dif["boxes"]}
        if cor:
            prov["camera"] = {"median_noise_corr": round(cor["median"], 3),
                              "matching_blocks": round(float((cor["grid"] >= 0.3).mean()), 3)}
        art = f"{key}_vs_{ref['id']}"
        _comparison_png(rp, path, dif["boxes"], cor, os.path.join(out_dir, art + ".png"))
        artifacts[art] = art + ".png"
        if dif["changed_fraction"] > 0.35:
            findings.append(finding("provenance", "registry-similar", "Resembles a registered image only in overall layout",
                                    f"Perceptual hash is close to registered image {ref['id']} ({ref['filename']}, distance {dist}/64), but {dif['changed_fraction']:.0%} of pixels differ, "
                                    "so it is most likely a different picture with a similar composition, not a copy. " + NOT_EVIDENCE,
                                    0.0, {"type": "file"}, artifact=art))
            prov["reference"]["layout_only"] = True
        elif dif["changed_fraction"] < 0.0005 and not dif["boxes"]:
            findings.append(finding("provenance", "registry-similar", "Re-encoded copy of a registered original",
                                    f"Perceptual hash is {dist}/64 bits from registered original {ref['id']} ({ref['filename']}) and no visual changes were found. "
                                    "It is the same picture, saved again (different bytes, same content).",
                                    0.0, {"type": "file"}, artifact=art))
        else:
            q = Image.open(path)
            sx = q.width / ref["width"] if ref.get("width") else 1.0
            sy = q.height / ref["height"] if ref.get("height") else 1.0
            main_box = dif["boxes"][0] if dif["boxes"] else None
            loc = {"type": "region", "bbox": [int(main_box[0] * sx), int(main_box[1] * sy), int(main_box[2] * sx), int(main_box[3] * sy)]} if main_box else {"type": "file"}
            findings.append(finding("provenance", "modified-copy", "Modified version of a registered original",
                                    f"Perceptual hash matches registered original {ref['id']} ({ref['filename']}, distance {dist}/64) but SHA-256 differs, and "
                                    f"{dif['changed_fraction']:.1%} of pixels changed in {len(dif['boxes'])} area(s). The largest changed area is at "
                                    + (f"x={main_box[0]}, y={main_box[1]}, {main_box[2]}x{main_box[3]} px." if main_box else "n/a."),
                                    0.8, loc, ["Authorised edit (crop, caption overlay, colour grade) by the original publisher"], artifact=art))
        # ---------- 4. camera / sensor-noise consistency
        if cor and prov["reference"].get("layout_only"):
            prov.pop("camera", None)
            prov["camera_note"] = "Closest registered image is a different picture, so no sensor-noise reference is available."
        elif cor:
            good = float((cor["grid"] >= 0.3).mean())
            if cor["median"] >= 0.3:
                findings.append(finding("provenance", "camera-consistency", "Sensor-noise pattern matches the registered original",
                                        f"In {good:.0%} of the image the fine noise pattern correlates with the original (median r = {cor['median']:.2f}). "
                                        "That indicates the uploaded file was derived from the same capture, not re-photographed or regenerated.",
                                        0.0, {"type": "file"}, artifact=art))
                thr = min(0.6, cor["median"] - 0.3)      # well below how well the rest of the image matches
                bad = cor["grid"] < thr
                if bad.any() and not dif["boxes"]:
                    # No visible change anywhere: scattered low-correlation blocks are what re-compression does to flat areas.
                    findings.append(finding("provenance", "camera-consistency", "Minor noise differences, consistent with re-compression",
                                            f"{int(bad.sum())} block(s) of {cor['block']}px correlate less with the original's noise, but no visible content changed. "
                                            "Saving the image again at a lower quality smooths noise in flat areas like this.",
                                            0.0, {"type": "file"}, artifact=art))
                elif bad.any():
                    ys, xs = np.where(bad)
                    b = cor["block"]
                    with Image.open(path) as q2:
                        qw, qh = q2.size
                    sx = qw / ref["width"] if ref.get("width") else 1.0
                    sy = qh / ref["height"] if ref.get("height") else 1.0
                    bb = [int(xs.min() * b * sx), int(ys.min() * b * sy), int((xs.max() - xs.min() + 1) * b * sx), int((ys.max() - ys.min() + 1) * b * sy)]
                    findings.append(finding("image", "camera-consistency", "Sensor-noise mismatch inside the image",
                                            f"{int(bad.sum())} block(s) of {b}px no longer carry the original's noise pattern (correlation below {thr:.2f}, while the rest of the image is {cor['median']:.2f}). "
                                            "These blocks have a measured local noise mismatch; editing, blur or processing are possible explanations, not confirmed AI object insertion.",
                                            0.75, {"type": "region", "bbox": bb},
                                            ["Local blur, retouching or text overlay applied by the publisher"], artifact=art))
            else:
                findings.append(finding("provenance", "camera-consistency", "Noise pattern differs from the registered original",
                                        f"The image looks like registered original {ref['id']} but its fine noise pattern does not correlate (median r = {cor['median']:.2f}). "
                                        "It may be a re-photograph, a heavily processed copy, or a regenerated look-alike.",
                                        0.45, {"type": "file"}, ["Strong re-compression or filtering", "Different frame from the same burst"]))
        else:
            prov["camera_note"] = "Uploaded image has a different resolution from the original, so a pixel-aligned noise comparison is not possible."
        # ---------- 5. metadata vs original
        notes = []
        if prov["reference"].get("layout_only"):
            ref = dict(ref, capture_date=None, device=None, software=meta.get("software"))
        if ref.get("capture_date") and meta.get("capture_date") and ref["capture_date"][:16] != meta["capture_date"][:16]:
            notes.append(f"capture date changed from {ref['capture_date']} to {meta['capture_date']}")
        if ref.get("device") and meta.get("device") and ref["device"] != meta["device"]:
            notes.append(f"camera changed from '{ref['device']}' to '{meta['device']}'")
        if meta.get("software") and meta.get("software") != ref.get("software"):
            notes.append(f"re-saved with '{meta['software']}' (original: {ref.get('software') or 'no editing software'})")
        if notes:
            findings.append(finding("image", "metadata-inconsistency", "Metadata differs from the registered original",
                                    "Compared with the registered original: " + "; ".join(notes) + ".",
                                    0.5 if any("date" in n or "camera" in n for n in notes) else 0.35,
                                    {"type": "metadata", "field": "vs registered original"}, ["Legitimate re-export by the publisher"]))
        if ref.get("c2pa") in ("valid", "valid_untrusted"):
            prov["reference_had_c2pa"] = True
    elif is_image:
        findings.append(finding("provenance", "registry", "Not found in the registry",
                                "No exact (SHA-256) or visually similar (perceptual hash) registered original. " + NOT_EVIDENCE +
                                " The registry only knows the originals that were added to it.", 0.0, {"type": "file"}))

    # ---------- 6. C2PA content credentials
    c2 = check_c2pa(path)
    prov["c2pa"] = c2
    st = c2["status"]
    if st == "valid":
        findings.append(finding("provenance", "c2pa", "Content credentials valid and trusted",
                                f"C2PA manifest signed by '{c2.get('signer')}' ({c2.get('generator')}). Signature and content hash verify, so the file is unchanged since signing. Recorded actions: {', '.join(filter(None, c2.get('actions', []))) or 'none'}.",
                                0.0, {"type": "metadata", "field": "C2PA"}))
    elif st == "valid_untrusted":
        findings.append(finding("provenance", "c2pa", "Content credentials intact (signer not on trust list)",
                                f"C2PA manifest signed by '{c2.get('signer')}' ({c2.get('generator')}). The signature and content hash verify, so the file is unchanged since signing, "
                                f"but the signing certificate is not on a trusted list. Recorded actions: {', '.join(filter(None, c2.get('actions', []))) or 'none'}.",
                                0.0, {"type": "metadata", "field": "C2PA"}, ["Demo / self-issued certificate"]))
    elif st == "invalid":
        findings.append(finding("provenance", "c2pa", "Content credentials broken",
                                "The file carries a C2PA manifest but validation failed (" + ", ".join(c2.get("failures", [])[:3]) + "). "
                                "This usually means the content was changed after it was signed.",
                                0.7, {"type": "metadata", "field": "C2PA"}, ["Damaged file / partial download"]))
    elif st == "present_unverified":
        findings.append(finding("provenance", "c2pa", "Content credentials present (not verified)", c2["detail"], 0.0, {"type": "metadata", "field": "C2PA"}))
    else:
        txt = "No C2PA content credentials. Most images in circulation have none. " + NOT_EVIDENCE
        if prov.get("reference_had_c2pa"):
            txt = ("No C2PA content credentials, although the registered original carried them. Editors and social platforms usually strip "
                   "credentials when re-saving, which fits a re-saved or edited copy. " + NOT_EVIDENCE)
        findings.append(finding("provenance", "c2pa", "No content credentials", txt, 0.0, {"type": "metadata", "field": "C2PA"}))
    if c2.get("ai_generated"):
        findings.append(finding("provenance", "c2pa", "Credentials declare AI-generated content",
                                "The C2PA manifest records that this content was produced by a generative AI model.",
                                0.6, {"type": "metadata", "field": "C2PA digitalSourceType"}))
    return {"findings": findings, "artifacts": artifacts, "provenance": prov}
