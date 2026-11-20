"""Export a case report as a PDF (reportlab)."""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak, KeepTogether
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from xml.sax.saxutils import escape as esc

BAND_COLOR = {"high": colors.HexColor("#c0392b"), "medium": colors.HexColor("#d68910"), "low": colors.HexColor("#1e8449")}


def _loc(loc):
    t = loc.get("type")
    if t == "region":
        x, y, w, h = loc["bbox"]
        return f"region x={x}, y={y}, {w}x{h}px"
    if t == "frame":
        s = f"frame at {loc['time']:.2f}s"
        if loc.get("bbox"):
            s += " region x={}, y={}, {}x{}px".format(*loc["bbox"])
        return s
    if t == "segment":
        return f"{loc['start']:.2f}s - {loc['end']:.2f}s"
    if t == "metadata":
        return f"metadata: {loc.get('field')}"
    if t == "text":
        return f"text: \"{loc.get('quote', '')[:60]}\""
    return "whole file"


KINDS = {"ver": ("Verified check", "#00a88a"), "cmp": ("Compared with original", "#2e86c1"), "heu": ("Heuristic indicator", "#d68910"),
         "exp": ("Experimental", "#8e6cf0"), "info": ("Info", "#7f8c8d")}


def kind_of(f):
    """Same evidence levels as the web interface: verified facts are kept apart from heuristic and experimental signals."""
    if f.get("experimental"):
        return "exp"
    if f.get("category") in ("registry", "registry-match", "registry-similar", "history") or "content credentials" in f.get("indicator", "").lower():
        return "ver"
    if f.get("modality") == "provenance":
        return "cmp"
    if f.get("score", 0) <= 0:
        return "info"
    return "heu"


def _signals(report):
    rows = []
    for m in ("image", "provenance", "video", "audio", "text"):
        F = [f for e in report["evidence"] if e.get("role") != "reference" for f in e["findings"] if f.get("modality") == m]
        if F:
            top = max(F, key=lambda f: f["score"])
            rows.append([m.capitalize(), f"{top['score']:.2f}", f"{sum(1 for f in F if f['score'] >= 0.4)} of {len(F)}", f"{top['id']} {top['indicator']}"])
    X = report.get("correlations", [])
    if X:
        rows.append(["Cross-modal", "-", f"{sum(1 for c in X if c['effect'] == 'strengthens')} of {len(X)}", "relations that strengthen the manipulation hypothesis"])
    return rows


def _waveform(wf, dur, segs, width):
    """Draw the stored waveform envelope (same decoded signal the analysis used) with flagged segments shaded."""
    h = 34 * mm
    d = Drawing(width, h)
    d.add(Rect(0, 0, width, h, fillColor=colors.HexColor("#0b141b"), strokeColor=None))
    for f in segs:
        a, b = f["location"]["start"] / dur * width, f["location"]["end"] / dur * width
        d.add(Rect(a, 0, max(1.5, b - a), h, fillColor=colors.HexColor("#5a3d0a"), strokeColor=None))
        d.add(String(a + 2, h - 9, f["id"], fontSize=6.5, fillColor=colors.HexColor("#f5a524")))
    n = wf["bins"]
    peak = max(1e-3, max(abs(v) for v in wf["max"] + wf["min"]))
    for i in range(n):
        x = (i + 0.5) / n * width
        d.add(Line(x, h / 2 + wf["min"][i] / peak * (h / 2 - 3), x, h / 2 + wf["max"][i] / peak * (h / 2 - 3), strokeColor=colors.HexColor("#1fd6c4"), strokeWidth=0.6))
    return d


def _people_section(pp, case_dir, W, body, small, h3):
    """Experimental people-consistency block: kept apart from the findings and the manipulation score."""
    out = [Spacer(1, 6), Paragraph("<font color='#8e6cf0'>EXPERIMENTAL</font> &middot; People consistency (not part of the manipulation score)", h3)]
    if pp.get("error"):
        return out + [Paragraph("Not available for this image: " + esc(pp["error"]), small)]
    out.append(Paragraph(f"Detector: {esc(pp.get('detector', ''))}. {esc(pp.get('detects', ''))}", small))
    out.append(Paragraph(esc(pp.get("summary", "")), body))
    if pp.get("artifact"):
        fp = os.path.join(case_dir, "artifacts", pp["artifact"])
        if os.path.exists(fp):
            img = RLImage(fp)
            r = img.imageHeight / img.imageWidth
            img.drawWidth, img.drawHeight = min(W, 160 * mm), min(W, 160 * mm) * r
            if img.drawHeight > 85 * mm:
                img.drawHeight, img.drawWidth = 85 * mm, 85 * mm / r
            out += [Spacer(1, 3), img, Paragraph("<i>Detected faces (inner box) and estimated head-and-shoulders regions (outer box); red = highlighted for review</i>", small)]
    people = pp.get("people") or []
    if people:
        fmt = lambda p, m: "-" if p["measures"].get(m) is None else f"{p['measures'][m]:.2f}" + ("" if p["z"].get(m) is None else f" (z {p['z'][m]:+.1f})")
        rows = [["Person", "Face box", "Noise", "Sharpness", "Blockiness", "Bright.", "Colour", "Outline step", "Review"]]
        for p in people:
            rows.append([p["id"], Paragraph(" ".join(str(v) for v in p["face_bbox"]), small)] + [Paragraph(fmt(p, m), small) for m in
                        ("noise", "sharpness", "blockiness", "brightness", "colour", "boundary")] + [Paragraph("<b>highlighted</b>" if p["flagged"] else "-", small)])
        t = Table(rows, colWidths=[13 * mm, 22 * mm] + [(W - 35 * mm - 18 * mm) / 6] * 6 + [18 * mm], repeatRows=1)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                               ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        out += [Spacer(1, 3), t]
        for p in people:
            if p["flagged"]:
                out.append(Paragraph(f"<b>{p['id']} highlighted for review:</b> " + esc("; ".join(p["reasons"])), small))
    out.append(Paragraph("<b>Possible innocent causes (false positives):</b> " + esc(" ".join(pp.get("false_positives", []))), small))
    out.append(Paragraph("<b>Limitations:</b> " + esc(" ".join(pp.get("limitations", []))), small))
    out.append(Paragraph("A highlighted person is not a finding that the person was inserted, removed or AI-generated; it marks where a human should look.", small))
    return out


def build_pdf(report, case_dir, out_path):
    ss = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=ss["BodyText"], fontSize=8, leading=10)
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=12.5)
    h1, h2, h3 = ss["Title"], ss["Heading2"], ss["Heading3"]
    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"TruthLens report {report['case_id']}", author="TruthLens")

    def frame(canvas, d):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#0b141b"))
        canvas.rect(0, A4[1] - 9 * mm, A4[0], 9 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor("#1fd6c4"))
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(16 * mm, A4[1] - 6 * mm, "TRUTHLENS FORENSIC REPORT")
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 6 * mm, f"Case {report['case_id']}")
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(16 * mm, 8 * mm, "Indicators are heuristic signals for a human investigator, not proof. Verified checks are exact hash/registry/credential results.")
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Page {d.page}")
        canvas.restoreState()
    W = A4[0] - 32 * mm
    s = []
    ov = report["overall"]
    if report.get("analysis_status") == "partial":
        s.append(Paragraph("<b>ANALYSIS INCOMPLETE:</b> A failed check is not a clean result. Original evidence is retained. Review the evidence limitations.", body))
    if any(e.get("reference_comparison", {}).get("regions") for e in report["evidence"] if e.get("reference_comparison")):
        s.append(Paragraph("<b>Reference comparison: changed regions observed.</b> Review the measured difference map and reference-comparison findings. Pixel differences do not identify AI use and do not add an artificial score boost.", body))
    s.append(Paragraph("TruthLens Forensic Report", h1))
    title = "" if report["title"] == f"Case {report['case_id']}" else f"<b>{esc(report['title'])}</b> &nbsp; "
    s.append(Paragraph(f"{title}Case {report['case_id']} &nbsp; Investigation UUID {report.get('investigation_uuid', 'n/a')}<br/>Generated {report['created_at']} &nbsp; TruthLens v{report['tool']['version']}", small))
    s.append(Spacer(1, 6))
    t = Table([[Paragraph(f"<font size=16 color='white'><b>{ov['score']:.2f}</b></font><br/><font size=8 color='white'>range {ov['range'][0]:.2f}-{ov['range'][1]:.2f}</font>", body),
                Paragraph(f"<b>{esc(ov['label'])}</b><br/>{esc(ov['disclaimer'])}", small)]], colWidths=[32 * mm, W - 32 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), BAND_COLOR.get(ov["band"], colors.grey)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
    s.append(t)
    s.append(Spacer(1, 8))
    s.append(Paragraph("Summary" + (" (AI-assisted)" if report.get("summary_source") == "ai" else ""), h2))
    s.append(Paragraph(esc(report["summary"]), body))
    s.append(Paragraph("A low manipulation indicator does not rule out AI generation or establish camera capture. The existing forensic indicator and its weights are unchanged.", small))
    assessments = report.get("assessments") or {}
    ai_generation = assessments.get("ai_generation")
    s.append(Paragraph("AI-generation assessment (separate from manipulation)", h2))
    if ai_generation:
        s.append(Paragraph(esc(ai_generation["label"]), h3))
        s.append(Paragraph("Confidence not reported: no statistically calibrated AI-image detector is configured.", small))
        for item in ai_generation.get("items", []):
            s.append(Paragraph(f"<b>{esc(item['evidence_id'])} - {esc(item['filename'])}:</b> {esc(item['label'])}", body))
            for entry in item["evidence"]:
                s.append(Paragraph(esc(entry["detail"]) + (" Refs: " + esc(", ".join(entry.get("refs", []))) if entry.get("refs") else ""), small))
            if not item["evidence"]:
                s.append(Paragraph("No positive generation evidence found in available checks; this does not prove camera capture.", small))
            s.append(Paragraph("Methods: " + esc("; ".join(m["name"] + ": " + m["status"] for m in item["methods"])), small))
        for limitation in ai_generation["limitations"]:
            s.append(Paragraph("Limitation: " + esc(limitation), small))
    else:
        s.append(Paragraph("Not assessed: this saved report predates the separate AI-generation assessment.", small))
    s.append(Paragraph("Content provenance and Sync ID", h3))
    for item in (assessments.get("content_provenance") or {}).get("items", []):
        s.append(Paragraph(esc(f"{item['evidence_id']}: C2PA {item['c2pa_status']}; declared signer {item.get('declared_signer') or 'unavailable'}; issuer trust {item['issuer_trust']}; independent origin {item['independently_verified_origin']}"), small))
    s.append(Paragraph("Sync ID: not assessed in this report. Use the explicit Sync ID verification tool for SHA-256 matching, Ed25519 signature verification and separate issuer trust reporting.", small))
    if ov.get("uncertainty_notes"):
        s.append(Paragraph("<i>Uncertainty: " + esc("; ".join(ov["uncertainty_notes"])) + "</i>", small))

    s.append(Paragraph("Contents", h3))
    s.append(Paragraph("1 Summary and strongest signals &middot; 2 Investigation &middot; 3 What this cannot establish &middot; 4 Cross-modal correlations &middot; "
                       "5 Evidence items (images, measurements, findings) &middot; 6 Recommended checks and limitations &middot; 7 Chain of custody &middot; 8 Method and evidence levels", small))
    sig = _signals(report)
    if sig:
        s.append(Paragraph("Strongest signal per engine", h3))
        t = Table([["Engine", "Strongest", "Findings >= 0.40", "Strongest finding"]] + [[r[0], r[1], r[2], Paragraph(esc(r[3]), small)] for r in sig],
                  colWidths=[28 * mm, 20 * mm, 28 * mm, W - 76 * mm])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                               ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        s.append(t)

    if report.get("investigation"):
        s.append(Paragraph("Investigation", h2))
        STC = {"match": "#00a88a", "indicator": "#c0392b", "neutral": "#7f8c8d", "clear": "#1e8449", "experimental": "#8e6cf0"}
        LBL = {"match": "MATCH", "indicator": "INDICATOR", "neutral": "INFO", "clear": "CLEAR", "experimental": "EXPERIMENTAL"}
        rows = [["Investigation", "Evidence", "Finding", "Status", "Refs"]]
        for r in report["investigation"]:
            rows.append([Paragraph(esc(r["investigation"]), small), Paragraph(esc(r["item"]), small), Paragraph(esc(r["result"]), small),
                         Paragraph(f"<font color='{STC.get(r['status'], '#000')}'><b>{LBL.get(r['status'], r['status'])}</b></font>", small),
                         Paragraph(", ".join(r["refs"][:5]), small)])
        t = Table(rows, colWidths=[34 * mm, 30 * mm, W - 110 * mm, 22 * mm, 24 * mm], repeatRows=1)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                               ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#f6f6f6"))]))
        s.append(t)
    if report.get("cannot_establish"):
        s.append(Paragraph("What this report cannot establish", h2))
        for c in report["cannot_establish"]:
            s.append(Paragraph("&bull; " + esc(c), body))
    s.append(Paragraph("Evidence correlations", h2))
    rows = [["ID", "Relation", "Effect", "Evidence"]]
    for c in report["correlations"]:
        rows.append([c["id"], Paragraph(f"<b>{esc(c['title'])}</b><br/>{esc(c['explanation'])}" + (" <i>(AI)</i>" if c.get("source") == "ai" else ""), small),
                     c["effect"], ", ".join(c["evidence_refs"] + c.get("finding_refs", [])[:4])])
    if len(rows) == 1:
        rows.append(["-", "No cross-modal relations (single media type or nothing to compare).", "", ""])
    t = Table(rows, colWidths=[12 * mm, W - 62 * mm, 20 * mm, 30 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    s.append(t)

    for e in report["evidence"]:
        s.append(PageBreak())
        s.append(Paragraph(f"{e['id']} &middot; {esc(e['filename'])} &middot; {e['modality']} &middot; item indicator {e['score']:.2f}", h2))
        s.append(Paragraph(f"Evidence UUID {e.get('uuid', 'n/a')} &middot; detected type {e.get('detected_type', '?')}<br/>SHA-256 {e['sha256']}<br/>{e['size_bytes']:,} bytes, received {e['received_at']}, stored as {esc(e['stored_as'])}"
                           + (f"<br/>{esc(e['note'])}" if e.get("note") else ""), small))
        m = e.get("metadata") or {}
        facts = [(k, m.get(k)) for k in ("device", "capture_date", "software", "creation_time", "encoder", "duration_s", "width", "height", "fps",
                                         "noise_level", "noise_floor_db_median", "pitch_jitter_median_pct") if m.get(k) not in (None, "", [], {})]
        if m.get("cuts"):
            facts.append(("hard cuts (s)", ", ".join(str(c) for c in m["cuts"][:12])))
        pv = e.get("provenance") or {}
        if pv:
            reg = pv.get("registry") or {}
            facts.append(("registry", f"exact {reg['exact']['id']}" if reg.get("exact") else (f"similar {reg['similar'][0]['id']} (pHash distance {reg['similar'][0]['distance']}/64)" if reg.get("similar") else "no match (not an indicator)")))
            facts.append(("C2PA", (pv.get("c2pa") or {}).get("status", "not checked")))
        if facts:
            t = Table([[Paragraph(esc(str(k)), small), Paragraph(esc(str(v))[:160], small)] for k, v in facts], colWidths=[40 * mm, W - 40 * mm])
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f6f7")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            s.append(Spacer(1, 4))
            s.append(t)
        if m.get("waveform", {}).get("bins") and m.get("duration_s"):
            segs = [f for f in e["findings"] if f["location"].get("type") == "segment"]
            s.append(Spacer(1, 4))
            s.append(_waveform(m["waveform"], m["duration_s"], segs, W))
            s.append(Paragraph(f"<i>Waveform of the analysed signal, 0-{m['duration_s']} s; shaded = flagged segments</i>", small))
        cited = [f for f in (m.get("frames") or []) if f.get("findings")][:6]
        if cited:
            cells = []
            for fr in cited:
                fp = os.path.join(case_dir, "artifacts", fr["thumb"])
                if os.path.exists(fp):
                    im = RLImage(fp)
                    r = im.imageHeight / im.imageWidth
                    im.drawWidth, im.drawHeight = W / 3 - 4, (W / 3 - 4) * r
                    cells.append([im, Paragraph(f"{fr['t']:.2f} s &middot; noise z {fr.get('noise_deficit_z')} &middot; {', '.join(fr['findings'])}", small)])
            if cells:
                rows = [[c[0] for c in cells[i:i + 3]] for i in range(0, len(cells), 3)]
                caps = [[c[1] for c in cells[i:i + 3]] for i in range(0, len(cells), 3)]
                grid = []
                for a, b in zip(rows, caps):
                    grid += [a + [""] * (3 - len(a)), b + [""] * (3 - len(b))]
                s.append(Spacer(1, 4))
                s.append(Paragraph("Frames cited by findings", h3))
                s.append(Table(grid, colWidths=[W / 3] * 3))
        for k in [k for k in e.get("artifacts", {}) if not k.endswith("_heat")][:3]:
            p = os.path.join(case_dir, "artifacts", e["artifacts"][k])
            if os.path.exists(p) and p.endswith(".png"):
                img = RLImage(p)
                ratio = img.imageHeight / img.imageWidth
                w = min(W, 160 * mm)
                img.drawWidth, img.drawHeight = w, w * ratio
                if img.drawHeight > 90 * mm:
                    img.drawHeight, img.drawWidth = 90 * mm, 90 * mm / ratio
                s.append(Spacer(1, 4))
                s.append(img)
                s.append(Paragraph(f"<i>{k}</i>", small))
        if e["findings"]:
            rows = [["ID", "Indicator / why", "Level", "Where / check", "Score"]]
            for f in sorted(e["findings"], key=lambda f: -f["score"]):
                txt = f"<b>{esc(f['indicator'])}</b><br/>{esc(f['explanation'])}"
                if f.get("alternatives"):
                    txt += "<br/><i>Alternative explanations: " + esc("; ".join(f["alternatives"])) + "</i>"
                kl, kc = KINDS[kind_of(f)]
                linked = [c["id"] for c in report.get("correlations", []) if f["id"] in (c.get("finding_refs") or [])]
                where = esc(_loc(f["location"])) + f"<br/><font color='#7f8c8d'>{esc(f.get('modality', ''))}/{esc(f.get('category', ''))}</font>" + (f"<br/>relations: {', '.join(linked)}" if linked else "")
                rows.append([f["id"], Paragraph(txt, small), Paragraph(f"<font color='{kc}'><b>{kl}</b></font>", small), Paragraph(where, small), f"{f['score']:.2f}\n{f['strength']}"])
            t = Table(rows, colWidths=[12 * mm, W - 96 * mm, 22 * mm, 42 * mm, 20 * mm], repeatRows=1)
            t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                                   ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            s.append(Spacer(1, 4))
            s.append(t)
        else:
            s.append(Paragraph("No indicators flagged for this item.", body))
        for l in e.get("limitations", []):
            s.append(Paragraph("Limitation: " + esc(l), small))
        if e.get("people"):
            s += _people_section(e["people"], case_dir, W, body, small, h3)

    s.append(PageBreak())
    s.append(Paragraph("Recommended human checks", h2))
    for c in report["recommended_checks"]:
        s.append(Paragraph("&bull; " + esc(c), body))
    s.append(Paragraph("Limitations", h2))
    for l in report["limitations"]:
        s.append(Paragraph("&bull; " + esc(l), small))
    s.append(Paragraph("Evidence integrity", h2))
    s.append(Paragraph(", ".join(f"{i['id']}: {'hash verified' if i['verified'] else 'HASH MISMATCH'}" for i in report["integrity"]), small))
    s.append(Paragraph("Analysis log (chain of custody)", h2))
    cv = report.get("custody_verification") or {}
    s.append(Paragraph(("Hash chain verified: " + str(cv.get("entries")) + " entries, head " + str(cv.get("head", ""))[:16] + "...") if cv.get("ok")
                       else f"<font color='red'>HASH CHAIN BROKEN at entry {cv.get('broken_at')} ({esc(str(cv.get('reason')))})</font>", small))
    if report.get("report_sha256"):
        s.append(Paragraph(f"Report JSON SHA-256 (logged at generation): {report['report_sha256']}", small))
    rows = [["#", "Time", "Event", "Detail", "Ref", "Entry hash"]] + [
        [str(l.get("seq", "")), l["time"][11:19], Paragraph(esc(l["step"]), small), Paragraph(esc(l["detail"]), small), l.get("ref") or "",
         Paragraph((l.get("entry_hash") or "")[:12], small)] for l in report["log"]]
    t = Table(rows, colWidths=[8 * mm, 15 * mm, 24 * mm, W - 85 * mm, 14 * mm, 24 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    s.append(t)
    s.append(Spacer(1, 6))
    s.append(Paragraph(esc(report["ai"]["note"]), small))
    s.append(Paragraph("Method and evidence levels", h2))
    for k in ("ver", "cmp", "heu", "exp", "info"):
        kl, kc = KINDS[k]
        expl = {"ver": "Exact and reproducible: SHA-256 hashes, exact registry matches, content-credential (C2PA) validation, earlier cases with the same file.",
                "cmp": "Measured against a registered original: pixel differences and sensor-noise correlation with that original.",
                "heu": "Hand-tuned statistical signals (sensor noise, error level, cuts, audio noise floor and pitch, text claims). Consistent with editing; not proof.",
                "exp": "Not validated on labelled real-world data (for example generator frequency analysis). Never used as a verdict on its own.",
                "info": "Context only; carries no weight in the indicator. Missing metadata, missing credentials or an unregistered file are never treated as manipulation."}[k]
        s.append(Paragraph(f"<font color='{kc}'><b>{kl}</b></font>: {expl}", small))
    s.append(Paragraph("Scores are heuristic strengths from 0 to 1, combined across independent signals and capped at 0.95; they are not probabilities.", small))
    doc.build(s, onFirstPage=frame, onLaterPages=frame)
    return out_path
