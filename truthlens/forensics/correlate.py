"""Cross-modal correlation (rule-based).

Compares signals across evidence items and modalities. Each correlation says whether it
STRENGTHENS or WEAKENS the case for manipulation, and which evidence/findings it links.
"""
from datetime import datetime

import numpy as np
from PIL import Image


def _corr(kind, title, explanation, effect, score, evidence_refs, finding_refs=None, source="rules"):
    return {"type": kind, "title": title, "explanation": explanation, "effect": effect,
            "score": round(score, 2), "evidence_refs": evidence_refs, "finding_refs": finding_refs or [],
            "source": source}


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _dhash(path, size=16):
    g = np.asarray(Image.open(path).convert("L").resize((size + 1, size)), dtype=np.int16)
    return (g[:, 1:] > g[:, :-1]).flatten()


def _diff_bbox(path_a, path_b):
    from PIL import ImageFilter
    a = Image.open(path_a).convert("RGB")
    b = Image.open(path_b).convert("RGB").resize(a.size)
    # blur first so re-compression noise does not count as a difference
    a, b = a.filter(ImageFilter.GaussianBlur(2)), b.filter(ImageFilter.GaussianBlur(2))
    d = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).mean(axis=2)
    mask = d > 6
    if mask.mean() < 0.0005:
        return None, float(mask.mean())
    ys, xs = np.where(mask)
    # robust box: ignore stray pixels
    x0, x1 = np.percentile(xs, [1, 99]).astype(int)
    y0, y1 = np.percentile(ys, [1, 99]).astype(int)
    return [int(x0), int(y0), int(x1 - x0), int(y1 - y0)], float(mask.mean())


def correlate(evidence, results, text_claims, ref_time):
    """evidence: list of evidence dicts; results: {evidence_id: analyzer result}."""
    out = []
    by_mod = {}
    for e in evidence:
        by_mod.setdefault(e["modality"], []).append(e)

    # ---------- 1. dates: caption vs media metadata
    media_dates = []
    for e in evidence:
        m = results.get(e["id"], {}).get("metadata", {})
        dt = _parse_dt(m.get("capture_date")) or _parse_dt(m.get("creation_time"))
        if dt:
            media_dates.append((e, dt, "EXIF capture date" if m.get("capture_date") else "container creation time"))
    # The same date stated in several texts (e.g. the caption and the post) is one claim: count it once,
    # otherwise every repeat adds another "strengthens" relation and inflates the overall indicator.
    date_claims = list({c["value"]: c for c in text_claims if c["type"] == "date"}.values())
    text_ids = [e["id"] for e in by_mod.get("text", [])]
    for c in date_claims:
        cd = datetime.fromisoformat(c["value"])
        for e, dt, label in media_dates:
            gap = abs((cd - dt).days)
            if gap > 2:
                out.append(_corr("contradiction", f"Claimed date vs {e['filename']} metadata",
                                 f"The text says '{c['text']}' ({cd:%d %b %Y}) but the {label} of {e['filename']} is {dt:%d %b %Y}, "
                                 f"{gap} days apart ({gap / 365:.1f} years). Old media re-shared as new is one of the most common forms of misinformation.",
                                 "strengthens", 0.75 if gap > 30 else 0.5, text_ids + [e["id"]]))
            else:
                out.append(_corr("consistency", f"Claimed date matches {e['filename']}",
                                 f"The text date ({cd:%d %b %Y}) is consistent with the {label} ({dt:%d %b %Y}).",
                                 "weakens", 0.3, text_ids + [e["id"]]))
    if date_claims and not media_dates and len(evidence) > len(text_ids):
        out.append(_corr("unverifiable", "Claimed date cannot be checked",
                         "The text makes a date claim but none of the media files carries capture-date metadata.",
                         "neutral", 0.0, text_ids))

    # ---------- 2. authenticity / liveness claims vs detected edits
    auth = any(c["type"] == "authenticity" for c in text_claims)
    live = any(c["type"] == "liveness" for c in text_claims)
    if auth or live:
        strong = [(e, f) for e in evidence if e["modality"] != "text"
                  for f in results.get(e["id"], {}).get("findings", [])
                  if f["score"] >= 0.5 and f["category"] in ("editing", "compositing", "face-or-region-manipulation",
                                                             "temporal-discontinuity", "synthetic-or-spliced-audio", "synthetic-generation")]
        if strong:
            names = sorted({e["filename"] for e, _ in strong})
            out.append(_corr("contradiction", "'Unedited / real' claim vs detected edit indicators",
                             f"The text claims the media is {'unedited/real' if auth else 'live/continuous'}, but {len(strong)} moderate-or-strong edit indicators were found in {', '.join(names)}.",
                             "strengthens", 0.7, text_ids + sorted({e['id'] for e, _ in strong}), [f["id"] for _, f in strong]))
    if live:
        for e in by_mod.get("video", []):
            cuts = results.get(e["id"], {}).get("metadata", {}).get("cuts", [])
            if cuts:
                out.append(_corr("contradiction", "'Live / continuous' claim vs hard cuts",
                                 f"The text describes live/continuous footage, but {e['filename']} contains {len(cuts)} hard cut(s) at {', '.join(f'{c:.2f}s' for c in cuts)}.",
                                 "strengthens", 0.65, text_ids + [e["id"]]))

    # ---------- 3. video cuts vs audio anomalies in the same file (time alignment)
    for e in by_mod.get("video", []):
        vres = results.get(e["id"], {})
        cuts = vres.get("metadata", {}).get("cuts", [])
        audio_items = [a for a in by_mod.get("audio", []) if a.get("derived_from") == e["id"]]
        for a in audio_items:
            for f in results.get(a["id"], {}).get("findings", []):
                loc = f["location"]
                if loc.get("type") != "segment":
                    continue
                near = [c for c in cuts if min(abs(c - loc["start"]), abs(c - loc["end"])) <= 0.6]
                vf = [vf["id"] for vf in vres.get("findings", []) if vf["location"].get("time") in near
                      or (vf["location"].get("type") == "segment" and abs(vf["location"].get("start", -9) - loc["start"]) <= 0.6)]
                if near and vres.get("metadata", {}).get("produced_video"):
                    out.append(_corr("partial", f"Audio change at a scene cut ({near[0]:.2f}s) in an edited video",
                                     f"The audio changes at {loc['start']:.2f}-{loc['end']:.2f}s, next to a hard cut. This video has many cuts (edited / produced content), "
                                     "where sound normally changes with the scene, so this is not treated as evidence of an edit.",
                                     "neutral", 0.2, [e["id"], a["id"]], [f["id"]]))
                elif near:
                    out.append(_corr("corroboration", f"Audio anomaly aligns with video cut at {near[0]:.2f}s",
                                     f"The suspicious audio segment ({loc['start']:.2f}-{loc['end']:.2f}s) starts or ends within 0.6s of a hard visual cut "
                                     f"({', '.join(f'{c:.2f}s' for c in near)}). Two independent signals pointing at the same moment make an edit at that point much more likely.",
                                     "strengthens", 0.8, [e["id"], a["id"]], [f["id"]] + vf))
                else:
                    out.append(_corr("partial", "Audio anomaly without a matching visual cut",
                                     f"The audio changes character at {loc['start']:.2f}-{loc['end']:.2f}s but the picture is continuous there. "
                                     "This fits voice replacement (dubbing / cloned voice over real video), but also an innocent change in background noise.",
                                     "neutral", 0.4, [e["id"], a["id"]], [f["id"]]))

    # ---------- 4. visual region manipulation seen in more than one source
    vis = [(e, f) for e in evidence if e["modality"] in ("image", "video")
           for f in results.get(e["id"], {}).get("findings", [])
           if f["category"] in ("compositing", "face-or-region-manipulation") and f["score"] >= 0.6]
    srcs = sorted({e["id"] for e, _ in vis})
    if len(srcs) >= 2:
        out.append(_corr("corroboration", "Same manipulation pattern in multiple visual sources",
                         "Low-noise (re-rendered) regions were found in " + ", ".join(sorted({e['filename'] for e, _ in vis})) +
                         ". A consistent pattern across separate files suggests the same editing tool or workflow.",
                         "strengthens", 0.6, srcs, [f["id"] for _, f in vis]))

    # ---------- 5. near-duplicate images: is one a modified copy of another?
    imgs = by_mod.get("image", [])
    for i in range(len(imgs)):
        for j in range(i + 1, len(imgs)):
            a, b = imgs[i], imgs[j]
            try:
                ham = int((_dhash(a["path"]) != _dhash(b["path"])).sum())
            except Exception:
                continue
            if ham > 40:
                continue
            box, frac = _diff_bbox(a["path"], b["path"])
            if box is None:
                out.append(_corr("consistency", f"{a['filename']} and {b['filename']} are the same picture",
                                 "The two images are visually identical (only re-compression differences).", "neutral", 0.0, [a["id"], b["id"]]))
                continue
            # which one is the edited copy? the one with more/stronger findings
            sa = sum(f["score"] for f in results[a["id"]]["findings"])
            sb = sum(f["score"] for f in results[b["id"]]["findings"])
            orig, edit = (a, b) if sa <= sb else (b, a)
            out.append(_corr("corroboration", f"{edit['filename']} is a modified copy of {orig['filename']}",
                             f"The images are near-duplicates (perceptual hash distance {ham}/256) but differ in a localized area "
                             f"x={box[0]}, y={box[1]}, {box[2]}x{box[3]} px ({frac:.1%} of pixels). "
                             f"{orig['filename']} has fewer manipulation indicators, so it is the likely earlier version. Compare the two side by side.",
                             "strengthens", 0.85, [orig["id"], edit["id"]],
                             [f["id"] for f in results[edit["id"]]["findings"] if f["location"].get("type") == "region"]))
            out[-1]["diff_bbox"] = box
            out[-1]["diff_on"] = edit["id"]
    for n, c in enumerate(out, 1):
        c["id"] = f"X{n:02d}"
    return out
