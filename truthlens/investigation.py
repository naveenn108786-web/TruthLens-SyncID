"""Builds the 'Investigation -> Finding' table and the 'What this cannot establish' list.

status values: match (positive provenance), indicator (possible manipulation),
neutral (information only, never evidence of manipulation), clear (checked, nothing found),
experimental.
"""

META_CATS = {"editing", "metadata", "metadata-inconsistency"}
REGION_CATS = {"compositing", "modified-copy"}


def _row(inv, item, result, status, refs=None):
    return {"investigation": inv, "item": item, "result": result, "status": status, "refs": refs or []}


def investigation_table(evidence, results, correlations, overall):
    rows = []
    for e in evidence:
        if e["modality"] != "image":
            continue
        r = results.get(e["id"], {})
        fs = r.get("findings", [])
        pv = r.get("provenance") or {}
        reg = pv.get("registry") or {}
        name = e["filename"] + (" (reference context)" if e.get("role") == "reference" else "")
        by = lambda pred: [f for f in fs if pred(f)]
        comparison = r.get("reference_comparison")
        if comparison:
            detail = comparison.get("detail", comparison["status"])
            rows.append(_row("Supplied reference comparison", name,
                             detail + " Pixel differences do not identify AI use; the reference's origin is not independently verified.",
                             "neutral", [f["id"] for f in by(lambda f: f["category"] == "reference-difference")]))
        # SHA-256
        if reg.get("exact"):
            rows.append(_row("SHA-256", name, f"Exact match to registered original {reg['exact']['id']} ({reg['exact']['filename']})", "match",
                             [f["id"] for f in by(lambda f: f["category"] == "registry-match")]))
        else:
            rows.append(_row("SHA-256", name, "No exact registered match", "neutral"))
        # Perceptual hash
        if reg.get("exact"):
            rows.append(_row("Perceptual hash", name, "Not needed (exact match)", "clear"))
        elif reg.get("similar"):
            s = reg["similar"][0]
            mod = by(lambda f: f["category"] == "modified-copy")
            lay = (pv.get("reference") or {}).get("layout_only")
            rows.append(_row("Perceptual hash", name,
                             f"Similar to registered image {s['id']} ({s['filename']}, distance {s['distance']}/64)" +
                             (": different picture with a similar layout" if lay else ", with changed content" if mod else ", same content"),
                             "neutral" if lay else "indicator" if mod else "match", [f["id"] for f in mod + by(lambda f: f["category"] == "registry-similar")]))
        else:
            rows.append(_row("Perceptual hash", name, "No visually similar registered image (not evidence of manipulation)", "neutral",
                             [f["id"] for f in by(lambda f: f["category"] == "registry")]))
        # Metadata
        m = by(lambda f: f["category"] in META_CATS and f["score"] > 0)
        info = by(lambda f: f["category"] in META_CATS and f["score"] == 0)
        if m:
            rows.append(_row("Metadata", name, "; ".join(f["indicator"] for f in m), "indicator", [f["id"] for f in m]))
        elif info:
            rows.append(_row("Metadata", name, "; ".join(f["indicator"] for f in info) + " (not evidence of manipulation)", "neutral", [f["id"] for f in info]))
        else:
            meta = r.get("metadata", {})
            rows.append(_row("Metadata", name, f"Consistent: {meta.get('device') or 'device n/a'}, captured {meta.get('capture_date') or 'n/a'}", "clear"))
        # C2PA
        c2 = pv.get("c2pa") or {}
        st = c2.get("status", "none")
        txt = {"valid": f"Valid, signed by {c2.get('signer')}", "valid_untrusted": f"Intact, signed by {c2.get('signer')} (signer not on trust list)",
               "invalid": "Present but BROKEN: content changed after signing", "present_unverified": "Present, not verified",
               "none": "No content credentials (common; not evidence of manipulation)"}.get(st, st)
        rows.append(_row("C2PA credentials", name, txt, {"valid": "match", "valid_untrusted": "match", "invalid": "indicator"}.get(st, "neutral"),
                         [f["id"] for f in by(lambda f: f["category"] == "c2pa")]))
        # Image analysis (regions)
        reg_f = by(lambda f: f["category"] in REGION_CATS and f["score"] >= 0.4)
        weak = by(lambda f: f["category"] in REGION_CATS and 0 < f["score"] < 0.4)
        clean = by(lambda f: f["category"] == "noise-absent")
        if reg_f:
            rows.append(_row("Image analysis", name, f"{len(reg_f)} suspicious modified region(s) identified", "indicator", [f["id"] for f in reg_f]))
        elif clean:
            rows.append(_row("Image analysis", name, "Almost no sensor noise anywhere (unusually clean image)", "indicator", [f["id"] for f in clean]))
        elif weak:
            rows.append(_row("Image analysis", name, f"Only weak anomalies ({len(weak)}), likely texture or edges", "clear", [f["id"] for f in weak]))
        else:
            rows.append(_row("Image analysis", name, "No localized anomalies", "clear"))
        # Camera consistency
        cam = pv.get("camera")
        cf = by(lambda f: f["category"] == "camera-consistency")
        if cam:
            bad = [f for f in cf if f["score"] > 0]
            rows.append(_row("Camera (sensor-noise) consistency", name,
                             f"Noise pattern matches the registered original in {cam['matching_blocks']:.0%} of the image" + ("; mismatch in edited area" if bad else ""),
                             "indicator" if bad else "match", [f["id"] for f in cf]))
        else:
            rows.append(_row("Camera (sensor-noise) consistency", name, pv.get("camera_note") or "No suitable reference image available", "neutral"))
        # Generator (experimental)
        g = r.get("generator") or {}
        gf = by(lambda f: f.get("experimental"))
        if gf:
            rows.append(_row("Generator analysis (experimental)", name, "Synthetic-like frequency indicators detected", "experimental", [f["id"] for f in gf]))
        elif g:
            rows.append(_row("Generator analysis (experimental)", name, "No synthetic-like frequency pattern" + ("" if g.get("peak_checked") else " (JPEG: periodic-peak test skipped)"), "clear"))
    # supporting multimodal checks
    for e in evidence:
        if e["modality"] in ("video", "audio", "text"):
            fs = [f for f in results.get(e["id"], {}).get("findings", []) if f["score"] >= 0.4]
            label = {"video": "Video (supporting)", "audio": "Audio (supporting)", "text": "Text / context (supporting)"}[e["modality"]]
            rows.append(_row(label, e["filename"], "; ".join(sorted({f["indicator"] for f in fs})) or "No moderate or strong indicators",
                             "indicator" if fs else "clear", [f["id"] for f in fs]))
    strong = [c for c in correlations if c.get("effect") == "strengthens"]
    weak = [c for c in correlations if c.get("effect") == "weakens"]
    if correlations:
        rows.append(_row("Cross-modal correlation", "all evidence",
                         f"{len(strong)} relation(s) strengthen and {len(weak)} weaken the manipulation hypothesis",
                         "indicator" if strong else "clear", [c["id"] for c in correlations][:8]))
    final = {"high": "Multiple indicators require human review", "medium": "Some indicators: human review recommended",
             "low": "No strong indicators found (this does not prove authenticity)"}[overall["band"]]
    rows.append(_row("Final assessment", "case", f"{final} (indicator {overall['score']:.2f}, range {overall['range'][0]:.2f}-{overall['range'][1]:.2f})",
                     {"high": "indicator", "medium": "indicator", "low": "clear"}[overall["band"]]))
    return rows


def cannot_establish(evidence, results):
    out = ["Whether the depicted event really happened, or who is shown. Forensics examines files, not the world.",
           "That a low manipulation indicator or absent AI-generation evidence proves camera capture. AI generation is assessed separately and can remain inconclusive.",
           "That media is authentic merely because no indicators were found: careful manipulation can leave no detectable trace.",
           "Who made an edit, or why."]
    imgs = [e for e in evidence if e["modality"] == "image"]
    pv = {e["id"]: results.get(e["id"], {}).get("provenance") or {} for e in imgs}
    if any(not (p.get("registry") or {}).get("exact") and not (p.get("registry") or {}).get("similar") for p in pv.values()):
        out.append("The original source of unregistered images. Not being in the registry is not evidence of manipulation; the registry only knows what was added to it.")
    if any((p.get("registry") or {}).get("exact") for p in pv.values()):
        out.append("That a registered original is itself truthful: an exact match proves the file is unchanged from the registered version, nothing more.")
    if any((p.get("c2pa") or {}).get("status") == "none" for p in pv.values()):
        out.append("The capture device or publisher of files without C2PA credentials. Missing credentials are normal and are not evidence of manipulation.")
    if any((p.get("c2pa") or {}).get("status") == "valid_untrusted" for p in pv.values()):
        out.append("The real-world identity of a C2PA signer whose certificate is not on a trust list.")
    if any(results.get(e["id"], {}).get("generator") for e in imgs):
        out.append("That an image is AI-generated: the generator analysis is experimental and is never used as a verdict on its own.")
    if any(e["modality"] in ("video", "audio") for e in evidence):
        out.append("That a voice belongs to a specific person: no speaker-verification model is used.")
    return out
