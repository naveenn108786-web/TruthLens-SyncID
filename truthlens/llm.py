"""Optional AI reading of the text + cross-modal summary using Claude.

The forensic detectors are deterministic; the LLM only (a) reads the caption/transcript
against the extracted evidence facts and (b) writes the investigator summary. Everything it
returns is labelled "AI-assisted" in the report and must cite finding / evidence ids.
If no API key is configured or the call fails, the rule-based output is used instead.
"""
import json
import os

MODEL = os.environ.get("TRUTHLENS_MODEL", "claude-opus-5-5")

SYSTEM = """You are a careful digital-forensics analyst assistant inside TruthLens.
You receive: a caption/transcript, metadata facts extracted from media files, automated forensic
findings (each with an id like F003 and a heuristic score), and rule-based cross-modal correlations
(ids like X01). Your job:
1. Check each factual claim in the text (dates, places, people, events, authenticity, liveness)
   against the evidence facts. Mark it contradicted, supported, or unverifiable, and cite ids.
2. Point out additional cross-modal relations the rules may have missed, citing ids.
3. Write a short, neutral summary for a human investigator (3-5 sentences).
Rules: never treat an image as fake because it is unregistered, lacks metadata or lacks C2PA credentials
(those are neutral). Never claim certainty; automated scores are indicators, not proof. Do not invent facts that are
not in the input. Prefer 'unverifiable' when evidence is missing. Keep each explanation under 50 words."""

SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "claim": {"type": "string"},
            "assessment": {"type": "string", "enum": ["contradicted", "supported", "unverifiable"]},
            "explanation": {"type": "string"},
            "refs": {"type": "array", "items": {"type": "string"}}},
            "required": ["claim", "assessment", "explanation", "refs"], "additionalProperties": False}},
        "cross_modal": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"},
            "explanation": {"type": "string"},
            "effect": {"type": "string", "enum": ["strengthens", "weakens", "neutral"]},
            "refs": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "explanation", "effect", "refs"], "additionalProperties": False}},
        "summary": {"type": "string"},
        "recommended_checks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["claims", "cross_modal", "summary", "recommended_checks"],
    "additionalProperties": False,
}


def available():
    if os.environ.get("TRUTHLENS_OFFLINE") == "1":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def ai_review(payload, timeout=45):
    """Return parsed dict or raise. payload is a compact dict of the case."""
    import anthropic
    client = anthropic.Anthropic(timeout=timeout, max_retries=1)
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": "Case data (JSON):\n" + json.dumps(payload, default=str)[:60000]}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)
