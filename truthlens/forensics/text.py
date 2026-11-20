"""Text and context analysis (rule-based part).

Extracts the checkable claims from a caption / transcript / message so they can be compared
with the media evidence: dates ("today", "19 Nov 2026"), authenticity claims ("unedited",
"100% real"), liveness claims ("live", "continuous") and places. Also flags manipulative
framing (urgency, "share before it gets deleted"). The LLM step (llm.py) adds a deeper
reading when an API key is available.
"""
import re
from datetime import datetime, timedelta

from .common import finding

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

AUTH_PHRASES = ["unedited", "not edited", "no edit", "nothing has been edited", "nothing edited", "100% real",
                "100 percent real", "real footage", "raw footage", "original footage", "genuine", "authentic", "not fake"]
LIVE_PHRASES = ["live", "continuous", "one take", "single take", "uncut", "unbroken"]
URGENCY = ["breaking", "share before", "before it gets deleted", "they don't want you", "must watch", "viral",
           "shocking", "exposed", "urgent", "forward to everyone"]


def _parse_dates(text, ref):
    out = []
    low = text.lower()
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(\d{4})\b", low):
        out.append((datetime(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))), m.group(0)))
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b", low):
        out.append((datetime(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))), m.group(0)))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", low):
        try:
            out.append((datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0)))
        except ValueError:
            pass
    if re.search(r"\btoday\b|\btonight\b|\bjust now\b|\bthis morning\b", low):
        out.append((ref, "today"))
    if re.search(r"\byesterday\b", low):
        out.append((ref - timedelta(days=1), "yesterday"))
    return out


def analyze_text(text, ref_time=None):
    ref = ref_time or datetime.now()
    low = text.lower()
    claims = []
    seen = {}
    for dt, raw in _parse_dates(text, ref):
        v = dt.date().isoformat()
        seen[v] = (seen[v] + " / " + raw) if v in seen else raw
    for v, raw in seen.items():
        claims.append({"type": "date", "value": v, "text": raw})
    auth = [p for p in AUTH_PHRASES if p in low]
    if auth:
        claims.append({"type": "authenticity", "value": "claims media is unedited/real", "text": ", ".join(auth)})
    live = [p for p in LIVE_PHRASES if re.search(r"\b" + re.escape(p) + r"\b", low)]
    if live:
        claims.append({"type": "liveness", "value": "claims live / continuous footage", "text": ", ".join(live)})
    places = re.findall(r"\b(?:at|in|near)\s+(?:the\s+)?([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", text)
    for p in dict.fromkeys(places):
        claims.append({"type": "place", "value": p, "text": p})

    findings = []
    urg = [u for u in URGENCY if u in low]
    excl = text.count("!")
    caps = sum(1 for w in re.findall(r"\b[A-Z]{4,}\b", text))
    if urg or excl >= 3 or caps >= 2:
        bits = []
        if urg:
            bits.append("urgency / virality phrases (" + ", ".join(f"'{u}'" for u in urg) + ")")
        if excl >= 3:
            bits.append(f"{excl} exclamation marks")
        if caps >= 2:
            bits.append(f"{caps} all-caps words")
        findings.append(finding("text", "manipulative-framing", "Manipulative / urgency framing",
                                "The text uses " + ", ".join(bits) + ". This framing is common in misinformation because it discourages verification. It says nothing about whether the media itself is real.",
                                0.3, {"type": "text", "quote": ", ".join(urg[:3]) or text[:60]},
                                ["Legitimate breaking-news style"]))
    if auth:
        findings.append(finding("text", "unverified-claim", "Unsupported authenticity claim",
                                f"The text asserts the media is authentic ({', '.join(auth)}) without any verifiable source. Such claims need to be checked against the evidence (see cross-modal findings).",
                                0.2, {"type": "text", "quote": auth[0]}, ["Sincere statement by the original recorder"]))
    return {"modality": "text", "metadata": {"length": len(text), "claims": claims}, "findings": findings,
            "artifacts": {}, "limitations": ["Rule-based claim extraction only understands common date formats and phrases; "
                                             "the AI reading (when enabled) covers paraphrases and other languages."]}
