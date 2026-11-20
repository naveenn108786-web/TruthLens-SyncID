"""Explicit reference comparison, independent of perceptual-registry retrieval.

Measures pixel differences; does not identify object semantics, authorship or AI use.
"""
import os
import cv2
import numpy as np
from PIL import Image, ImageOps

from .common import finding, heat_colormap


def compare_images(reference, submitted, output_dir, key, reference_id):
    with Image.open(reference) as image:
        original = ImageOps.exif_transpose(image).convert("RGB")
    with Image.open(submitted) as image:
        query = ImageOps.exif_transpose(image).convert("RGB")
    result = {"reference_id": reference_id, "reference_trust": "user_supplied_not_independently_verified",
              "method": "Aligned-layout RGB difference after mild Gaussian smoothing; threshold 8/255",
              "ai_object_attribution": "not_established", "confidence": None, "regions": [], "artifacts": {}, "findings": [],
              "limitations": ["Differences can result from ordinary editing, lighting, resizing or encoding; they do not identify an AI-added object.",
                              "Comparison requires the same scene/layout. Crops, perspective shifts and unrelated references require manual alignment/review."]}
    if abs(original.width / original.height - query.width / query.height) / (original.width / original.height) > 0.02:
        return dict(result, status="not_comparable", detail="Aspect ratios differ; no automatic crop/perspective alignment was assumed.")
    scale = min(1.0, 1600 / max(query.size))
    size = (max(1, round(query.width * scale)), max(1, round(query.height * scale)))
    a = np.asarray(original.resize(size, Image.Resampling.LANCZOS), dtype=np.float32)
    b = np.asarray(query.resize(size, Image.Resampling.LANCZOS), dtype=np.float32)
    difference = np.abs(cv2.GaussianBlur(a, (0, 0), 1) - cv2.GaussianBlur(b, (0, 0), 1)).mean(axis=2)
    mask = (difference > 8).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    areas = sorted((row for row in stats[1:] if row[4] >= max(16, mask.size * 0.0002)), key=lambda row: -row[4])[:12]
    regions = [[round(int(row[j]) / scale) for j in range(4)] for row in areas]
    heat = heat_colormap(np.clip(difference / 40, 0, 1))
    image = Image.fromarray(heat)
    artifact = key + "_reference_diff"
    image.save(os.path.join(output_dir, artifact + ".png"))
    result.update(status="compared", significant_change_fraction=round(float(mask.mean()), 6), regions=regions,
                  analysis_width=size[0], analysis_height=size[1], threshold=8,
                  artifacts={artifact: artifact + ".png"}, detail=f"{len(regions)} changed region(s) relative to the supplied reference.")
    for box in regions:
        result["findings"].append(finding("image", "reference-difference", "Changed region versus supplied reference",
            "This region exceeds the 8/255 smoothed RGB difference threshold relative to " + reference_id +
            ". It confirms an observable difference, not its cause, authorization, or use of AI.",
            0.0, {"type": "region", "bbox": box}, ["Ordinary editing", "Local lighting or encoding differences", "Misaligned reference"], artifact=artifact))
    return result
