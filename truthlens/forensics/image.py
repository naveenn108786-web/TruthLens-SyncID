"""Image authenticity analysis.

Three explainable techniques:
1. Error Level Analysis (ELA): re-save the image as JPEG at a known quality and measure how
   much each area changes. Regions pasted in from another source often have a different
   compression history, so they "light up" differently from the rest of the picture.
2. Noise residual consistency: every camera sensor leaves a fine, fairly uniform noise
   pattern. Areas that were smoothed, AI-generated or pasted in tend to have noticeably
   less (or more) noise than their surroundings.
3. Metadata (EXIF / PNG text) checks: editing software signatures, AI generator tags,
   missing camera data, and capture vs modification date mismatches.
"""
import io
import os
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageDraw, ExifTags

from .common import finding, robust_z, heat_colormap, save_png

BLOCK = 16          # analysis block size in pixels
MAX_SIDE = 1600     # downscale very large images for speed

EDITORS = ["photoshop", "gimp", "lightroom", "snapseed", "picsart", "canva", "affinity",
           "pixelmator", "facetune", "faceapp", "remini", "paint.net", "fotor"]
AI_MARKERS = ["stable diffusion", "midjourney", "dall-e", "dall·e", "firefly", "imagen",
              "novelai", "comfyui", "automatic1111", "sdxl", "flux", "c2pa", "ai generated",
              "made with ai", "generated"]


# ---------------------------------------------------------------- pixel analysis

def _block_reduce(arr, block, fn=np.mean):
    h, w = arr.shape[:2]
    hb, wb = h // block, w // block
    a = arr[:hb * block, :wb * block].reshape(hb, block, wb, block)
    return fn(fn(a, axis=3), axis=1) if fn is np.mean else fn(a, axis=(1, 3))


def ela_map(rgb, quality=90):
    """Per-pixel error level: |original - resaved JPEG|, averaged over colour channels."""
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=quality)
    resaved = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), dtype=np.int16)
    return np.abs(rgb.astype(np.int16) - resaved).mean(axis=2).astype(np.float32)


def noise_map(rgb):
    """Per-block standard deviation of the high-frequency noise residual."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    residual = gray - cv2.medianBlur(gray.astype(np.uint8), 3).astype(np.float32)
    h, w = gray.shape
    hb, wb = h // BLOCK, w // BLOCK
    r = residual[:hb * BLOCK, :wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK)
    g = gray[:hb * BLOCK, :wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK)
    std = r.std(axis=(1, 3))
    mean = g.mean(axis=(1, 3))
    # clipped (pure black / pure white) blocks have no noise for innocent reasons
    valid = (mean > 10) & (mean < 245)
    return std, valid


def texture_map(rgb):
    """Per-block gradient energy: how much edge/texture detail each block contains."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    g = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    return _block_reduce(g, BLOCK)


def ela_residual_z(ela_grid, tex_grid):
    """ELA is naturally high on edges and texture. Fit log(ELA) against log(texture) and
    return how far each block is from what its texture predicts (robust z-score)."""
    x = np.log(tex_grid.ravel() + 1.0)
    y = np.log(ela_grid.ravel() + 0.05)
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    return robust_z(resid).reshape(ela_grid.shape)


def _components(mask, min_blocks=4):
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= min_blocks:
            yield (x, y, w, h), labels == i, area


def find_anomalous_regions(rgb, use_ela=True):
    """Return (regions, heat_grid).

    regions: list of dicts {kind, bbox:[x,y,w,h] in pixels, noise_z, noise_ratio, ela_z, ela_ratio, size_frac}
    """
    std, valid = noise_map(rgb)
    nz = np.zeros_like(std)
    if valid.sum() > 10:
        nz[valid] = robust_z(np.log(std[valid] + 1e-3))
    ez = np.zeros_like(std)
    ela_grid = None
    if use_ela:
        ela_grid = _block_reduce(ela_map(rgb), BLOCK)[:std.shape[0], :std.shape[1]]
        tex = texture_map(rgb)[:std.shape[0], :std.shape[1]]
        ez = ela_residual_z(ela_grid, tex)

    masks = {
        "smooth": (nz < -3.5) & valid,   # unusually clean -> smoothed / synthetic / pasted
        "noisy": (nz > 6.0) & valid,     # unusually noisy -> pasted from another source
        "ela": ez > 6.0,                 # compresses differently than its texture predicts
    }
    total = std.size
    med_std = np.median(std[valid]) if valid.any() else 1.0
    regions = []
    for kind, m in masks.items():
        for (x, y, w, h), sel, area in _components(m):
            regions.append({
                "kind": kind,
                "bbox": [int(x * BLOCK), int(y * BLOCK), int(w * BLOCK), int(h * BLOCK)],
                "noise_z": float(np.median(nz[sel])),
                "noise_ratio": float(np.median(std[sel]) / (med_std + 1e-6)),
                "ela_z": float(np.median(ez[sel])),
                "ela_ratio": float(np.median(ela_grid[sel]) / (np.median(ela_grid) + 1e-6)) if ela_grid is not None else None,
                "size_frac": float(area / total),
            })
    regions.sort(key=lambda r: -max(abs(r["noise_z"]) / 3.5, abs(r["ela_z"]) / 5.0) * r["size_frac"] ** 0.25)
    heat = np.clip(np.maximum(np.maximum(-nz, 0) / 8.0, np.maximum(ez, 0) / 10.0), 0, 1)
    return regions[:5], heat


def describe_region(r):
    ela_note = ""
    if r.get("ela_ratio") is not None:
        ela_note = f" Its JPEG error level is {r['ela_ratio']:.1f}x the image median."
    if r["kind"] == "smooth":
        return ("compositing", "Locally inconsistent noise (unusually smooth region)",
                f"This area has about {r['noise_ratio']:.0%} of the sensor-noise level of the rest of the image "
                f"(robust z = {r['noise_z']:.1f}). Smoothed, AI-generated or pasted regions often lose the camera's natural noise." + ela_note,
                ["Out-of-focus area or beauty filter", "Strong local noise reduction", "Very flat surface (sky, wall)"])
    if r["kind"] == "noisy":
        return ("compositing", "Locally inconsistent noise (unusually noisy region)",
                f"This area has {r['noise_ratio']:.1f}x the noise of the rest of the image (z = {r['noise_z']:.1f}), "
                "which can happen when content from a different camera or a lower-quality source is pasted in." + ela_note,
                ["Fine texture such as foliage, hair or fabric", "Low-light region amplified by the camera"])
    return ("compositing", "Error-level anomaly (different compression history)",
            f"After accounting for texture, this area changes much more on re-compression than the rest (z = {r['ela_z']:.1f}). "
            "That suggests it was saved a different number of times or at a different quality, as with a pasted region." + ela_note,
            ["Text or graphic overlays", "Sharpening applied to part of the image"])


def region_score(r):
    """Heuristic strength. Missing sensor noise is the most specific signal we have; noisy
    patches and ELA hotspots have many innocent causes (text, sharp edges), so they are capped lower."""
    if r["kind"] == "smooth":
        # Real JPEG photos have naturally clean areas (sky, defocus, flat walls) down to about z = -6;
        # only stronger deficits (z <= about -6.5) reach "moderate". Tested on real camera photos, see samples/eval_controlled.py.
        s = 0.15 + 0.7 * min(max(abs(r["noise_z"]) - 3.5, 0.0) / 8.5, 1.0)
    elif r["kind"] == "noisy":
        s = 0.25 + 0.25 * min(r["noise_z"] / 12.0, 1.0)
    else:
        s = 0.2 + 0.25 * min(r["ela_z"] / 12.0, 1.0)
    if r["size_frac"] > 0.4:     # an "anomaly" covering most of the image is not localized
        s *= 0.5
    return s


def draw_overlay(rgb, heat, boxes, path, scale=1.0):
    """Blend the heatmap over the image and draw labelled boxes."""
    h, w = rgb.shape[:2]
    hm = cv2.resize(heat.astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST)
    colored = heat_colormap(hm).astype(np.float32)
    alpha = (0.15 + 0.55 * hm)[..., None]
    out = (rgb.astype(np.float32) * (1 - alpha) + colored * alpha).astype(np.uint8)
    img = Image.fromarray(out)
    d = ImageDraw.Draw(img)
    for label, (x, y, bw, bh) in boxes:
        x, y, bw, bh = [int(v * scale) for v in (x, y, bw, bh)]
        d.rectangle([x, y, x + bw, y + bh], outline=(0, 255, 200), width=3)
        d.rectangle([x, max(0, y - 18), x + 8 * len(label) + 8, y], fill=(0, 255, 200))
        d.text((x + 4, max(0, y - 16)), label, fill=(0, 0, 0))
    img.save(path, "PNG")


def save_heat_layer(heat, w, h, path):
    """The same suspicious-region map as the heatmap, alone and transparent where nothing was measured,
    so the interface can lay it over the untouched image at any opacity."""
    hm = cv2.resize(heat.astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST)
    rgba = np.dstack([heat_colormap(hm), (np.clip(hm, 0, 1) * 255).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(path, "PNG")


# ---------------------------------------------------------------- metadata

def _parse_exif_date(s):
    try:
        return datetime.strptime(str(s).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def read_metadata(img):
    meta = {"format": img.format, "width": img.width, "height": img.height, "exif": {}}
    exif = img.getexif()
    tags = {}
    for k, v in exif.items():
        tags[ExifTags.TAGS.get(k, str(k))] = v
    try:
        for k, v in exif.get_ifd(0x8769).items():   # Exif sub-IFD holds DateTimeOriginal
            tags[ExifTags.TAGS.get(k, str(k))] = v
    except Exception:
        pass
    try:
        gps = exif.get_ifd(0x8825)
        if gps:
            tags["GPSInfo"] = {ExifTags.GPSTAGS.get(k, k): str(v) for k, v in gps.items()}
    except Exception:
        pass
    for k, v in tags.items():
        if isinstance(v, bytes):
            continue
        meta["exif"][k] = v if isinstance(v, (dict, int, float)) else str(v)[:200]
    # PNG / WebP text chunks often carry AI generator parameters
    meta["text_chunks"] = {k: str(v)[:300] for k, v in (img.info or {}).items()
                           if isinstance(v, str) and k not in ("exif", "icc_profile")}
    ex = meta["exif"]
    cap = _parse_exif_date(ex.get("DateTimeOriginal", "")) or _parse_exif_date(ex.get("DateTime", ""))
    meta["capture_date"] = cap.isoformat() if cap else None
    meta["device"] = " ".join(str(ex.get(k, "")) for k in ("Make", "Model")).strip() or None
    meta["software"] = ex.get("Software")
    return meta


def metadata_findings(meta):
    out = []
    ex = meta["exif"]
    blob = " ".join([str(ex.get("Software", "")), str(ex.get("ImageDescription", "")),
                     str(ex.get("Artist", ""))] + [f"{k} {v}" for k, v in meta["text_chunks"].items()]).lower()
    # Generic 'c2pa' / 'generated' text also appears in genuine camera workflows.
    # Require an explicit generation/tool declaration; missing metadata stays neutral.
    from ..assessment import DECLARATIONS
    match = DECLARATIONS.search(blob)
    ai_hit = match.group(0) if match else None
    if ai_hit:
        out.append(finding("image", "synthetic-generation", "Metadata declares AI tool use (unverified)",
                           f"The file's metadata mentions '{ai_hit}'. This editable declaration does not independently prove AI generation or exclude minor AI-assisted editing.",
                           0.85, {"type": "metadata", "field": "Software/text"},
                           ["Metadata can be added or edited by anyone", "Tool may have been used only for minor edits"]))
    sw = str(ex.get("Software", "")).lower()
    editor = next((e for e in EDITORS if e in sw), None)
    if editor:
        out.append(finding("image", "editing", "Editing software signature",
                           f"EXIF 'Software' field is '{ex.get('Software')}'. The image was last saved by an editing tool.",
                           0.5, {"type": "metadata", "field": "Software"},
                           ["Routine crop, resize or colour correction", "Export from a photo manager"]))
    if not ex:
        out.append(finding("image", "metadata", "No camera metadata",
                           "The image has no EXIF data, so capture time and device cannot be verified. This alone is not evidence of manipulation.",
                           0.0, {"type": "metadata", "field": "EXIF"},
                           ["Social media and messaging apps strip metadata on upload", "Screenshots have no EXIF"]))
    elif not meta["device"] and ex:
        out.append(finding("image", "metadata", "Camera make/model missing",
                           "EXIF exists but has no camera make/model, so the capture device cannot be verified. This alone is not evidence of manipulation.",
                           0.0, {"type": "metadata", "field": "Make/Model"},
                           ["Some apps re-write EXIF and drop device fields"]))
    orig = _parse_exif_date(ex.get("DateTimeOriginal", ""))
    mod = _parse_exif_date(ex.get("DateTime", ""))
    if orig and mod and abs((mod - orig).total_seconds()) > 86400:
        out.append(finding("image", "metadata", "Modified long after capture",
                           f"Captured {orig:%Y-%m-%d} but the file was modified on {mod:%Y-%m-%d}.",
                           0.4 if editor else 0.15, {"type": "metadata", "field": "DateTime vs DateTimeOriginal"},
                           ["Later re-export or archiving", "Camera import/transfer software (e.g. Nikon Transfer) rewrites the modified date"]))
    return out


# ---------------------------------------------------------------- entry point

def analyze_image(path, out_dir, key):
    img = Image.open(path)
    meta = read_metadata(img)
    rgb = np.asarray(img.convert("RGB"))
    scale = 1.0
    if max(rgb.shape[:2]) > MAX_SIDE:
        scale = MAX_SIDE / max(rgb.shape[:2])
        rgb = cv2.resize(rgb, (int(rgb.shape[1] * scale), int(rgb.shape[0] * scale)), interpolation=cv2.INTER_AREA)

    is_jpeg = (meta["format"] or "").upper() in ("JPEG", "JPG", "MPO")
    findings = metadata_findings(meta)
    std, valid = noise_map(rgb)
    global_noise = float(np.median(std[valid])) if valid.any() else 0.0
    meta["noise_level"] = round(global_noise, 2)
    if global_noise < 1.0:
        # No sensor noise anywhere: comparing "local" noise levels would only produce false regions.
        regions, heat = [], np.zeros_like(std)
        findings.append(finding("image", "noise-absent", "Almost no sensor noise anywhere in the image",
                                f"Median noise level is {global_noise:.2f} (camera photos in our tests: about 3-5). Real camera captures always carry some sensor noise; "
                                "this image is unusually clean throughout.",
                                0.4 if global_noise < 0.5 else 0.15, {"type": "file"},
                                ["Screenshot, illustration or rendered graphic", "Heavy noise reduction or beauty filter", "Upscaled or AI-enhanced image",
                                 "Heavy re-compression by a social platform (removes most noise)"]))
    else:
        regions, heat = find_anomalous_regions(rgb, use_ela=is_jpeg)
    boxes = []
    for r in regions:
        cat, ind, expl, alts = describe_region(r)
        f = finding("image", cat, ind, expl, region_score(r),
                    {"type": "region", "bbox": [int(v / scale) for v in r["bbox"]]}, alts, artifact=f"{key}_heatmap")
        findings.append(f)
        boxes.append((f["id"], r["bbox"]))

    draw_overlay(rgb, heat, boxes, os.path.join(out_dir, f"{key}_heatmap.png"))
    save_heat_layer(heat, rgb.shape[1], rgb.shape[0], os.path.join(out_dir, f"{key}_heat.png"))
    if is_jpeg:
        ela_vis = heat_colormap(np.clip(cv2.resize(ela_map(rgb), (rgb.shape[1], rgb.shape[0])) / 12.0, 0, 1))
        save_png(ela_vis, os.path.join(out_dir, f"{key}_ela.png"))

    limitations = [
        "Pixel checks look for local inconsistencies; a fully AI-generated or fully re-rendered image can be internally consistent.",
        "Heavy recompression (e.g. WhatsApp, Instagram) weakens noise and ELA signals.",
    ]
    if not is_jpeg:
        limitations.append("ELA skipped: it is only meaningful for JPEG files.")
    if scale < 1:
        limitations.append(f"Image downscaled to {rgb.shape[1]}x{rgb.shape[0]} for analysis.")
    artifacts = {f"{key}_heatmap": f"{key}_heatmap.png", f"{key}_heat": f"{key}_heat.png"}
    if is_jpeg:
        artifacts[f"{key}_ela"] = f"{key}_ela.png"
    return {"modality": "image", "metadata": meta, "findings": findings,
            "artifacts": artifacts, "limitations": limitations}
