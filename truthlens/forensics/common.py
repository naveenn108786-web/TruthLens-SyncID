"""Shared helpers: finding objects, score helpers, colour maps."""
import itertools
import numpy as np
from PIL import Image

_counter = itertools.count(1)


def finding(modality, category, indicator, explanation, score, location=None,
            alternatives=None, evidence=None, artifact=None):
    """One flagged observation.

    score: 0..1 strength of this single indicator (heuristic, not a probability).
    location: where it was found, e.g. {"type": "region", "bbox": [x, y, w, h]},
              {"type": "frame", "time": 4.0, "bbox": [...]}, {"type": "segment", "start": 2.5, "end": 3.0},
              {"type": "metadata", "field": "Software"}.
    alternatives: innocent explanations a human reviewer should rule out.
    """
    return {
        "id": f"F{next(_counter):03d}",
        "modality": modality,
        "category": category,
        "indicator": indicator,
        "explanation": explanation,
        "score": round(float(max(0.0, min(1.0, score))), 2),
        "strength": strength_label(score),
        "location": location or {"type": "file"},
        "alternatives": alternatives or [],
        "evidence": evidence,      # filled in by the pipeline (evidence id)
        "artifact": artifact,      # optional artifact key showing this finding
    }


def strength_label(score):
    if score <= 0.0:
        return "info"      # neutral information, never counted as a manipulation signal
    if score >= 0.7:
        return "strong"
    if score >= 0.4:
        return "moderate"
    return "weak"


def robust_z(values):
    """Robust z-score using median and MAD, so a few outliers don't hide themselves."""
    v = np.asarray(values, dtype=np.float64)
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826 + 1e-9
    return (v - med) / mad


def heat_colormap(x):
    """Map 0..1 array to an RGB 'inferno-like' palette without matplotlib."""
    x = np.clip(x, 0, 1)
    stops = np.array([[0, 0, 4], [87, 16, 110], [188, 55, 84], [249, 142, 9], [252, 255, 164]], dtype=np.float64)
    pos = x * (len(stops) - 1)
    i = np.clip(pos.astype(int), 0, len(stops) - 2)
    t = (pos - i)[..., None]
    rgb = stops[i] * (1 - t) + stops[i + 1] * t
    return rgb.astype(np.uint8)


def save_png(arr_rgb, path):
    Image.fromarray(arr_rgb).save(path, "PNG")
