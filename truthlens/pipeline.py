"""TruthLens investigation pipeline: INGEST -> ANALYZE -> CORRELATE -> REVIEW -> REPORT.

Evidence handling:
- uploads are validated by content (validation.py) before anything is stored
- every investigation and evidence item gets a UUID and a row in SQLite (db.py)
- every uploaded file is stored unchanged in data/cases/<case>/evidence/ (read-only) with its
  SHA-256 hash, size and receive time; all analysis works on copies/derived files
- every step is written to the hash-chained custody log (tamper-evident)
- hashes are re-verified and the report's own SHA-256 is logged when the report is produced
"""
import hashlib
import json
import mimetypes
import os
import re
import stat
import time
import traceback
import uuid
from datetime import datetime

from .forensics.image import analyze_image
from .forensics.video import analyze_video, extract_audio_track
from .forensics.audio import analyze_audio
from .forensics.text import analyze_text
from .forensics.correlate import correlate
from .forensics.provenance import analyze_provenance
from .forensics.generator import analyze_generator
from .forensics.persons import analyze_people
from .investigation import investigation_table, cannot_establish
from .assessment import build_assessments
from .forensics.comparison import compare_images
from . import llm, db
from .validation import validate_file, validate_batch, ValidationError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("TRUTHLENS_DATA", os.path.join(ROOT, "data", "cases"))
VERSION = "0.3.0"

EXT = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"},
    "audio": {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".opus"},
    "text": {".txt", ".md", ".srt", ".vtt"},
}


def modality_of(name):
    ext = os.path.splitext(name.lower())[1]
    for m, exts in EXT.items():
        if ext in exts:
            return m
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(name):
    name = os.path.basename(name or "file")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "file"


class Log:
    """Writes every step to the hash-chained custody log in SQLite (and keeps a local copy)."""
    def __init__(self):
        self.entries = []
        self.t0 = time.time()
        self.investigation_id = None

    def __call__(self, step, detail, ref=None, data_sha256=None):
        e = db.log_event(step, detail, investigation_id=self.investigation_id, ref=ref, data_sha256=data_sha256)
        e.update({"step": step, "elapsed_s": round(time.time() - self.t0, 2)})
        self.entries.append(e)


# ------------------------------------------------------------------ scoring

def evidence_score(findings):
    """Noisy-OR of finding scores: several weak signals add up, one strong signal dominates.
    Weak findings (< 0.4) are discounted because they have many innocent explanations."""
    p = 1.0
    for f in findings:
        s = f["score"] * (0.4 if f["score"] < 0.4 else 0.8)
        p *= 1 - s
    return round(1 - p, 2)


def overall_assessment(ev_scores, correlations, evidence, results):
    # each evidence item and each cross-modal link is treated as a partly independent signal;
    # weights < 1 keep the score from racing to 1.0 when many correlated signals fire
    vals = sorted(ev_scores.values(), reverse=True) or [0.0]
    p = 1 - 0.75 * vals[0]                 # the most suspicious item counts most
    for s in vals[1:]:
        p *= 1 - 0.3 * s                   # further items add supporting weight
    for c in correlations:
        if c["effect"] == "strengthens":
            p *= 1 - 0.25 * c["score"]
    score = min(0.95, 1 - p)     # never report certainty
    for c in correlations:
        if c["effect"] == "weakens":
            score *= 1 - 0.3 * c["score"]
    # uncertainty: wider when evidence is thin or degraded, narrower when modalities agree
    width = 0.12
    reasons = []
    flagged_mods = {e["modality"] for e in evidence if ev_scores.get(e["id"], 0) >= 0.4}
    if len({e["modality"] for e in evidence}) == 1:
        width += 0.06
        reasons.append("only one type of media was supplied, so nothing could be cross-checked")
    if any(not results.get(e["id"], {}).get("metadata", {}).get("exif") and e["modality"] == "image" for e in evidence):
        width += 0.04
        reasons.append("some images carry no metadata")
    n_corr = sum(1 for c in correlations if c["type"] == "corroboration")
    if n_corr:
        width -= min(0.05, 0.02 * n_corr)
        reasons.append(f"{n_corr} independent cross-modal corroboration(s) narrow the range")
    if len(flagged_mods) >= 3:
        width -= 0.02
    width = max(0.05, width)
    lo, hi = max(0.0, score - width), min(0.97, score + width)
    if score >= 0.65:
        band, label = "high", "Strong indicators of manipulation"
    elif score >= 0.35:
        band, label = "medium", "Some indicators of manipulation: human review needed"
    else:
        band, label = "low", "No strong indicators of manipulation found"
    return {"score": round(score, 2), "range": [round(lo, 2), round(hi, 2)], "band": band, "label": label,
            "uncertainty_notes": reasons,
            "disclaimer": "This score is a heuristic indicator built from the signals listed in this report. It is not a probability "
                          "that the media is fake and not a final verdict. Conclusions must be reviewed by a human investigator."}


def rule_summary(evidence, ev_scores, results, correlations, overall):
    lines = []
    # only real indicators: neutral info (unregistered, no metadata, no C2PA) is never presented as one
    top = sorted(((f, e) for e in evidence for f in results.get(e["id"], {}).get("findings", []) if f["score"] > 0),
                 key=lambda x: -x[0]["score"])[:4]
    lines.append(f"TruthLens examined {len(evidence)} evidence item(s) across "
                 f"{len({e['modality'] for e in evidence})} media type(s). Overall: {overall['label'].lower()} "
                 f"(indicator {overall['score']:.2f}, range {overall['range'][0]:.2f}-{overall['range'][1]:.2f}).")
    if top:
        lines.append("Strongest indicators: " + "; ".join(
            f"{f['indicator'].lower()} in {e['filename']} ({f['id']}, {f['strength']})" for f, e in top) + ".")
    strong_c = [c for c in correlations if c["effect"] == "strengthens"]
    if strong_c:
        lines.append("Cross-modal: " + "; ".join(c["title"] for c in strong_c[:3]) + ".")
    prov = [f"{e['filename']} is an exact match to registered original {results[e['id']]['provenance']['registry']['exact']['id']}"
            for e in evidence if ((results.get(e["id"], {}).get("provenance") or {}).get("registry") or {}).get("exact")]
    prov += [f"{e['filename']} is a modified version of registered original {results[e['id']]['provenance']['reference']['id']}"
             for e in evidence if any(f["category"] == "modified-copy" for f in results.get(e["id"], {}).get("findings", []))]
    if prov:
        lines.append("Provenance: " + "; ".join(prov) + ".")
    if not top:
        lines.append("No manipulation indicators were found. That does not prove the media is authentic.")
    lines.append("Each indicator has innocent alternative explanations listed in the report; verify with the original source before drawing conclusions.")
    return " ".join(lines)


def recommended_checks(evidence, results, correlations):
    checks = []
    mods = {e["modality"] for e in evidence}
    if any(c["type"] == "contradiction" and "date" in c["title"].lower() for c in correlations):
        checks.append("Run a reverse image/video search to find the earliest published copy and its date.")
    if "image" in mods or "video" in mods:
        checks.append("Obtain the original file from the device that recorded it (not a re-shared copy) and compare hashes.")
    if any(f["location"].get("type") in ("region", "frame") for e in evidence for f in results.get(e["id"], {}).get("findings", [])):
        checks.append("Visually inspect flagged regions at full resolution for edges, lighting and shadow mismatches.")
    if "audio" in mods:
        checks.append("Listen to flagged audio segments; compare the speaker's voice with verified recordings.")
    if "text" in mods:
        checks.append("Contact the named people or organisations to confirm or deny the claims in the text.")
    checks.append("Have a second analyst review this report independently before any publication or action.")
    return checks


# ------------------------------------------------------------------ main entry

def run_case(files, caption="", title="", case_id=None, reference_image=None):
    """files: list of (filename, bytes). Returns the report dict (also saved to disk).
    Raises ValidationError (before anything is stored) if an upload is invalid."""
    # ---------------- validate everything first: nothing is stored for a rejected upload
    files = list(files)
    reference_index = len(files) if reference_image else None
    if reference_image:
        files.append(reference_image)
    validate_batch(files)
    checks = [validate_file(name, data) for name, data in files]
    if reference_image and (checks[-1]["modality"] != "image" or not any(c["modality"] == "image" for c in checks[:-1])):
        raise ValidationError("A reference must be an image, with at least one submitted image to compare")

    log = Log()
    case_id = case_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    investigation_id = db.create_investigation(case_id, title or f"Case {case_id}")
    log.investigation_id = investigation_id
    cdir = os.path.join(DATA, case_id)
    edir, adir = os.path.join(cdir, "evidence"), os.path.join(cdir, "artifacts")
    os.makedirs(edir, exist_ok=True)
    os.makedirs(adir, exist_ok=True)
    created = datetime.now()
    log("investigation.create", f"Investigation {case_id} opened (UUID {investigation_id})")

    evidence = []
    texts = []
    for upload_index, ((name, data), chk) in enumerate(zip(files, checks)):
        mod = chk["modality"]
        safe = _safe_name(name)
        eid = f"E{len(evidence) + 1}"
        path = os.path.join(edir, f"{eid}_{safe}")
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)    # preserve original: read-only
        item = {"id": eid, "filename": safe, "modality": mod, "detected_type": chk["detected_type"], "sha256": sha256(path),
                "size_bytes": len(data), "mime": mimetypes.guess_type(name)[0], "received_at": created.isoformat(timespec="seconds"),
                "stored_as": os.path.relpath(path, cdir), "path": path, "validation_warnings": chk["warnings"]}
        item["uuid"] = db.add_evidence(investigation_id, item)
        if reference_index is not None and upload_index == reference_index:
            item["role"] = "reference"
            item["note"] = "User-supplied reference; original capture/authorship not independently established"
        evidence.append(item)
        log("evidence.ingest", f"Validated as {chk['detected_type']} ({mod}); stored {safe} read-only ({len(data):,} bytes); evidence UUID {item['uuid']}",
            eid, item["sha256"])
        for w in chk["warnings"]:
            log("evidence.validation", w, eid)
        if mod == "text":
            texts.append(data.decode("utf-8", errors="replace"))
    caption = (caption or "").strip()
    if caption:
        path = os.path.join(edir, f"E{len(evidence) + 1}_caption.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(caption)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        item = {"id": f"E{len(evidence) + 1}", "filename": "caption.txt", "modality": "text", "detected_type": "text", "sha256": sha256(path),
                "size_bytes": len(caption.encode()), "mime": "text/plain", "received_at": created.isoformat(timespec="seconds"),
                "stored_as": os.path.relpath(path, cdir), "path": path, "note": "Caption / context typed by the investigator"}
        item["uuid"] = db.add_evidence(investigation_id, item)
        evidence.append(item)
        log("evidence.ingest", f"Stored accompanying caption text; evidence UUID {item['uuid']}", item["id"], item["sha256"])
        texts.append(caption)

    # ---------------- analyze each item
    results = {}
    for e in list(evidence):
        key = e["id"]
        try:
            if e["modality"] == "image":
                # Manipulation detection engine (pixel + metadata checks)
                results[key] = analyze_image(e["path"], adir, key)
                # Authenticity & provenance engine (registry, C2PA, history, reference comparisons)
                pv = analyze_provenance(e["path"], e["sha256"], results[key]["metadata"], adir, key, investigation_id, DATA)
                results[key]["findings"] = pv["findings"] + results[key]["findings"]
                results[key]["artifacts"].update(pv["artifacts"])
                results[key]["provenance"] = pv["provenance"]
                log("analysis.provenance", _prov_log(pv["provenance"]), key)
                # Experimental generator-fingerprint analysis
                from PIL import Image as _I
                rgb = __import__("numpy").asarray(_I.open(e["path"]).convert("RGB"))
                gen = analyze_generator(rgb, adir, key, (results[key]["metadata"].get("format") or "").upper() in ("JPEG", "MPO"))
                results[key]["generator"] = {k: v for k, v in gen.items() if k != "finding"}
                results[key]["artifacts"][gen["artifact"]] = gen["artifact"] + ".png"
                if gen.get("finding"):
                    results[key]["findings"].append(gen["finding"])
                log("analysis.experimental", f"generator analysis raw score {gen['raw_score']}", key)
                # Experimental people-consistency analysis: reported separately, adds no finding and no score
                try:
                    ppl = analyze_people(rgb, adir, key)
                    log("analysis.experimental", f"people consistency (not scored): {ppl['count']} face(s) detected, "
                        f"highlighted: {', '.join(ppl['flagged']) or 'none'}", key)
                except Exception as ex:  # the feature is optional; the main analysis must not fail because of it
                    ppl = {"experimental": True, "affects_score": False, "error": f"{type(ex).__name__}: {ex}", "people": []}
                    log("analysis.experimental", f"people consistency unavailable: {type(ex).__name__}: {ex}", key)
                results[key]["people"] = ppl
            elif e["modality"] == "video":
                results[key] = analyze_video(e["path"], adir, key)
                pv = analyze_provenance(e["path"], e["sha256"], {}, adir, key, investigation_id, DATA, is_image=False)
                results[key]["findings"] = [f for f in pv["findings"] if f["category"] != "c2pa" or f["score"] > 0] + results[key]["findings"]
                results[key]["provenance"] = pv["provenance"]
                track = os.path.join(adir, f"{key}_audio.wav")
                if results[key]["metadata"].get("has_audio"):
                    aid = None
                    try:
                        if extract_audio_track(e["path"], track):
                            aid = f"{key}a"
                            d_item = {"id": aid, "filename": f"{e['filename']} (audio track)", "modality": "audio", "detected_type": "wav",
                                      "sha256": sha256(track), "size_bytes": os.path.getsize(track), "mime": "audio/wav",
                                      "received_at": created.isoformat(timespec="seconds"), "stored_as": os.path.relpath(track, cdir),
                                      "path": track, "derived_from": key, "note": f"Extracted from {e['id']} with ffmpeg (16 kHz mono)"}
                            d_item["uuid"] = db.add_evidence(investigation_id, d_item)
                            evidence.append(d_item)
                            log("evidence.derive", f"Extracted audio track from {e['filename']}", aid, d_item["sha256"])
                            results[aid] = analyze_audio(track, adir, aid)
                            log("analysis.run", f"audio analysis completed: {len(results[aid]['findings'])} finding(s)", aid)
                    except Exception as ex:
                        results[key]["analysis_status"] = "partial"
                        results[key]["limitations"].append(f"Analysis failed: audio extraction/analysis: {type(ex).__name__}: {ex}. Video results and original evidence preserved.")
                        if aid:
                            results[aid] = {"modality": "audio", "metadata": {}, "findings": [], "artifacts": {}, "analysis_status": "failed",
                                            "limitations": [f"Analysis failed: {type(ex).__name__}: {ex}"]}
                        log("analysis.error", f"Audio extraction/analysis failed; video results preserved: {ex}", key)
            elif e["modality"] == "audio":
                results[key] = analyze_audio(e["path"], adir, key)
            elif e["modality"] == "text":
                with open(e["path"], encoding="utf-8", errors="replace") as f:
                    results[key] = analyze_text(f.read(), created)
            else:
                results[key] = {"modality": e["modality"], "metadata": {}, "findings": [], "artifacts": {},
                                "limitations": ["File type not supported by this prototype."]}
            nf = len(results[key]["findings"])
            log("analysis.run", f"{e['modality']} analysis completed: {nf} finding(s), "
                f"{sum(1 for f in results[key]['findings'] if f['score'] > 0)} indicator(s)", key)
        except Exception as ex:
            traceback.print_exc()
            results[key] = {"modality": e["modality"], "metadata": {}, "findings": [], "artifacts": {},
                            "analysis_status": "failed", "limitations": [f"Analysis failed: {type(ex).__name__}: {ex}"]}
            log("analysis.error", f"{e['modality']} analysis failed: {ex}", key)
    supplied_reference = next((e for e in evidence if e.get("role") == "reference"), None)
    if supplied_reference:
        for item in evidence:
            if item["modality"] != "image" or item.get("role") == "reference":
                continue
            try:
                comparison = compare_images(supplied_reference["path"], item["path"], adir, item["id"], supplied_reference["id"])
                results[item["id"]]["reference_comparison"] = {k: v for k, v in comparison.items() if k not in ("findings", "artifacts")}
                results[item["id"]]["findings"].extend(comparison["findings"])
                results[item["id"]]["artifacts"].update(comparison["artifacts"])
                log("analysis.compare", comparison.get("detail", comparison["status"]), item["id"])
            except Exception as ex:
                results[item["id"]]["limitations"].append(f"Reference comparison unavailable: {type(ex).__name__}: {ex}")
    for e in evidence:
        for f in results.get(e["id"], {}).get("findings", []):
            f["evidence"] = e["id"]

    # ---------------- correlate
    # A supplied reference is comparison context, not a second suspect submission.
    # Do not infer edit direction from its own unrelated metadata/heuristic score.
    subjects = [e for e in evidence if e.get("role") != "reference"]
    claims = [c for e in subjects if e["modality"] == "text" for c in results[e["id"]]["metadata"].get("claims", [])]
    correlations = correlate(subjects, results, claims, created)
    for e in subjects:
        pv = results.get(e["id"], {}).get("provenance", {})
        if (pv.get("registry") or {}).get("exact"):
            correlations.append({"type": "provenance", "title": f"{e['filename']} is a registered original",
                                 "explanation": "Its SHA-256 matches a registered original exactly, so weak pixel-level indicators in it are most likely false alarms.",
                                 "effect": "weakens", "score": 0.9, "evidence_refs": [e["id"]], "finding_refs": [], "source": "rules"})
        if (pv.get("c2pa") or {}).get("status") in ("valid", "valid_untrusted"):
            correlations.append({"type": "provenance", "title": f"{e['filename']} has intact content credentials",
                                 "explanation": "Its C2PA signature and content hash verify: the file has not changed since it was signed.",
                                 "effect": "weakens", "score": 0.6, "evidence_refs": [e["id"]], "finding_refs": [], "source": "rules"})
        fs = results.get(e["id"], {}).get("findings", [])
        gen = [f for f in fs if f.get("experimental")]
        clean = [f for f in fs if f["category"] == "noise-absent"]
        if gen and clean:
            correlations.append({"type": "corroboration", "title": f"Two independent synthetic-like cues agree in {e['filename']}",
                                 "explanation": "The image has almost no sensor noise AND an upsampling-like frequency pattern. Each cue alone is weak; together they justify a closer human look (e.g. reverse image search, asking the uploader for the original).",
                                 "effect": "strengthens", "score": 0.5, "evidence_refs": [e["id"]], "finding_refs": [f["id"] for f in gen + clean], "source": "rules"})
    for n, c in enumerate(correlations, 1):
        c["id"] = f"X{n:02d}"
    log("correlation.run", f"{len(correlations)} relation(s) between evidence items found by rules")

    # ---------------- AI-assisted reading (optional)
    ai = {"used": False, "model": None, "note": "AI reading disabled (no API key); rule-based analysis only."}
    ai_out = None
    if llm.available() and (texts or len(evidence) > 1):
        payload = {
            "case_time": created.isoformat(timespec="seconds"),
            "text": texts,
            "evidence": [{"id": e["id"], "file": e["filename"], "modality": e["modality"],
                          "facts": _facts(results.get(e["id"], {}).get("metadata", {})),
                          "provenance": _prov_log(results[e["id"]]["provenance"]) if results.get(e["id"], {}).get("provenance") else None}
                         for e in subjects],
            "findings": [{"id": f["id"], "evidence": f["evidence"], "indicator": f["indicator"], "score": f["score"],
                          "location": f["location"], "explanation": f["explanation"]}
                         for e in subjects for f in results.get(e["id"], {}).get("findings", [])],
            "rule_correlations": [{k: c[k] for k in ("id", "type", "title", "explanation", "effect")} for c in correlations],
        }
        try:
            ai_out = llm.ai_review(payload)
            ai = {"used": True, "model": llm.MODEL, "note": "Claims check, extra correlations and summary are AI-assisted and cite evidence ids; verify them against the evidence."}
            log("analysis.ai", f"AI review completed ({llm.MODEL})")
        except Exception as ex:
            ai["note"] = f"AI reading unavailable ({type(ex).__name__}); rule-based analysis only."
            log("analysis.ai", f"AI review failed, using rules: {ex}")
    if ai_out:
        text_ids = [e["id"] for e in evidence if e["modality"] == "text"]
        from .forensics.common import finding
        for c in ai_out.get("claims", []):
            if c["assessment"] == "contradicted":
                f = finding("text", "contradicted-claim", f"Claim contradicted by evidence: \"{c['claim'][:80]}\"",
                            c["explanation"], 0.6, {"type": "text", "quote": c["claim"][:120]},
                            ["The evidence metadata itself may be wrong or altered"])
            elif c["assessment"] == "unverifiable":
                f = finding("text", "unverifiable-claim", f"Claim cannot be verified: \"{c['claim'][:80]}\"",
                            c["explanation"], 0.15, {"type": "text", "quote": c["claim"][:120]})
            else:
                continue
            f["evidence"] = text_ids[0] if text_ids else None
            f["refs"] = c.get("refs", [])
            f["source"] = "ai"
            if text_ids:
                results[text_ids[0]]["findings"].append(f)
        for c in ai_out.get("cross_modal", []):
            correlations.append({"id": f"X{len(correlations) + 1:02d}", "type": "ai-observation", "title": c["title"],
                                 "explanation": c["explanation"], "effect": c["effect"], "score": 0.0,
                                 "evidence_refs": [r for r in c.get("refs", []) if r.startswith("E")],
                                 "finding_refs": [r for r in c.get("refs", []) if r.startswith("F")], "source": "ai"})
        ai["claims"] = ai_out.get("claims", [])

    # ---------------- score
    ev_scores = {e["id"]: evidence_score(results.get(e["id"], {}).get("findings", [])) for e in evidence}
    subject_scores = {e["id"]: ev_scores[e["id"]] for e in subjects}
    overall = overall_assessment(subject_scores, correlations, subjects, results)
    log("assessment", f"Overall: {overall['label']} (indicator {overall['score']:.2f})")

    # ---------------- integrity re-check
    integrity = []
    for e in evidence:
        ok = os.path.exists(e["path"]) and sha256(e["path"]) == e["sha256"]
        integrity.append({"id": e["id"], "verified": ok})
    log("integrity.verify", "All evidence hashes re-verified" if all(i["verified"] for i in integrity) else "HASH MISMATCH detected")

    summary = (ai_out or {}).get("summary") or rule_summary(subjects, subject_scores, results, correlations, overall)
    checks = (ai_out or {}).get("recommended_checks") or recommended_checks(subjects, results, correlations)
    limitations = sorted({l for r in results.values() for l in r.get("limitations", [])})
    limitations.append("Scores are hand-tuned heuristics, not trained or benchmark-calibrated classifiers. They rank how unusual a signal is, not the probability of manipulation.")

    report = {
        "case_id": case_id, "investigation_uuid": investigation_id,
        "title": title or f"Case {case_id}", "created_at": created.isoformat(timespec="seconds"),
        "tool": {"name": "TruthLens", "version": VERSION},
        "overall": overall, "summary": summary, "summary_source": "ai" if ai_out and ai_out.get("summary") else "rules",
        "assessments": build_assessments(evidence, results, overall),
        "analysis_status": "partial" if any(r.get("analysis_status") in ("partial", "failed") for r in results.values()) else "complete",
        "recommended_checks": checks, "ai": ai,
        "evidence": [{k: v for k, v in e.items() if k != "path"} | {"score": ev_scores.get(e["id"], 0),
                     "metadata": _clean(results.get(e["id"], {}).get("metadata", {})),
                     "findings": results.get(e["id"], {}).get("findings", []),
                     "artifacts": results.get(e["id"], {}).get("artifacts", {}),
                     "limitations": results.get(e["id"], {}).get("limitations", []),
                     "analysis_status": results.get(e["id"], {}).get("analysis_status", "complete"),
                     "reference_comparison": results.get(e["id"], {}).get("reference_comparison"),
                     "provenance": _clean(results.get(e["id"], {}).get("provenance")),
                     "generator": _clean(results.get(e["id"], {}).get("generator")),
                     "people": _clean(results.get(e["id"], {}).get("people"))} for e in evidence],
        "correlations": correlations,
        "investigation": investigation_table(evidence, results, correlations, overall),
        "cannot_establish": cannot_establish(evidence, results),
        "limitations": limitations,
        "integrity": integrity,
        "log": log.entries,
    }
    rpath = os.path.join(cdir, "report.json")
    with open(rpath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    rsha = sha256(rpath)
    db.finish_investigation(investigation_id, overall["label"], overall["score"], os.path.relpath(rpath, DATA), rsha)
    log("report.generate", "Report written and its hash recorded", "report.json", rsha)
    return load_case(case_id)


def _prov_log(p):
    r = p.get("registry") or {}
    if r.get("exact"):
        reg = f"exact registry match {r['exact']['id']}"
    elif r.get("similar"):
        reg = f"similar to {r['similar'][0]['id']} (pHash distance {r['similar'][0]['distance']})"
    else:
        reg = "no registry match"
    return f"SHA-256/pHash: {reg}; C2PA: {(p.get('c2pa') or {}).get('status')}; prior cases: {len(p.get('history') or [])}"


def _facts(meta):
    keep = ("capture_date", "device", "software", "creation_time", "encoder", "duration_s", "cuts", "claims",
            "noise_floor_db_median", "pitch_jitter_median_pct", "possible_splice_points", "width", "height", "fps", "gps")
    out = {k: meta[k] for k in keep if meta.get(k) not in (None, [], {})}
    if meta.get("exif", {}).get("GPSInfo"):
        out["gps"] = meta["exif"]["GPSInfo"]
    return out


def _clean(meta):
    return json.loads(json.dumps(meta, default=str))


def list_cases():
    out = []
    for r in db.list_investigations():
        if r["status"] != "reported":
            continue
        band = "high" if (r["indicator"] or 0) >= 0.65 else "medium" if (r["indicator"] or 0) >= 0.35 else "low"
        item = {"case_id": r["case_ref"], "uuid": r["id"], "title": r["title"], "created_at": r["created_at"],
                "score": r["indicator"] or 0.0, "band": band}
        item.update(_case_summary(r["case_ref"]))
        out.append(item)
    return out


def _case_summary(cid):
    """Counts for the dashboard, read from the stored report (empty if it cannot be read)."""
    try:
        with open(os.path.join(DATA, cid, "report.json"), encoding="utf-8") as f:
            r = json.load(f)
    except Exception:
        return {}
    F = [f for e in r.get("evidence", []) for f in e.get("findings", [])]
    mods = {}
    for e in r.get("evidence", []):
        mods[e["modality"]] = mods.get(e["modality"], 0) + 1
    top = max(F, key=lambda f: f.get("score", 0), default=None)
    return {"label": (r.get("overall") or {}).get("label"), "modalities": mods, "evidence_count": len(r.get("evidence", [])),
            "indicators": sum(1 for f in F if f.get("score", 0) >= 0.4), "findings": len(F),
            "correlations": len(r.get("correlations", [])),
            "top_finding": {"id": top["id"], "indicator": top["indicator"], "score": top["score"]} if top and top.get("score", 0) > 0 else None}


def load_case(cid):
    """Report JSON plus the live custody log and chain/report integrity checks from the database."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cid or ""):
        return None
    inv = db.investigation_by_ref(cid)
    p = os.path.join(DATA, cid, "report.json")
    if not inv or not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        r = json.load(f)
    r["log"] = db.custody_for(inv["id"])
    r["custody_verification"] = db.verify_chain()
    r["report_sha256"] = inv["report_sha256"]
    r["report_integrity"] = sha256(p) == inv["report_sha256"]
    return r


def case_dir(cid):
    return os.path.join(DATA, cid)
