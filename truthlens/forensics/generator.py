"""EXPERIMENTAL: frequency-domain indicators of AI-generated / synthetically upsampled images.

Image generators build pictures by repeatedly upsampling feature maps. That tends to leave:
1. periodic peaks in the noise spectrum (a faint grid / checkerboard pattern)
2. a high-frequency deficit: too little fine-grained energy compared with a camera photo,
   whose sensor noise spreads energy evenly up to the highest frequencies
We measure both on the noise residual of the image. This is a known research direction
(e.g. spectral artifacts of GAN/diffusion upsampling), not a trained detector, so the score is
capped low and the result is always labelled experimental.
"""
import os

import cv2
import numpy as np
from PIL import Image

from .common import finding, heat_colormap

N = 512
EXPERIMENTAL_CAP = 0.35


def _residual(gray):
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    return gray - blur


def spectrum_features(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    h, w = gray.shape
    s = min(h, w, N)
    y0, x0 = (h - s) // 2, (w - s) // 2
    crop = gray[y0:y0 + s, x0:x0 + s]
    win = np.outer(np.hanning(s), np.hanning(s))
    c = s // 2
    yy, xx = np.mgrid[0:s, 0:s]
    r = np.hypot(yy - c, xx - c) / c                 # 0 = DC, 1 = Nyquist
    # 1) high-frequency deficit, measured on the image itself
    P = np.fft.fftshift(np.abs(np.fft.fft2((crop - crop.mean()) * win)) ** 2)
    hi = P[(r > 0.6) & (r <= 0.95)].mean()
    mid = P[(r > 0.25) & (r <= 0.45)].mean()
    hf_ratio = float(hi / (mid + 1e-9))
    # 2) periodic peaks in the noise residual: strongest isolated spike vs its neighbourhood
    F = np.fft.fftshift(np.abs(np.fft.fft2(_residual(crop) * win)))
    logF = np.log(F + 1e-6)
    med = cv2.medianBlur(logF.astype(np.float32), 5)
    spikes = logF - med
    spikes[r < 0.15] = 0
    peak = float(spikes.max())
    return {"hf_ratio": round(hf_ratio, 4), "peak": round(peak, 2), "size": s}, logF


def analyze_generator(rgb, out_dir, key, is_jpeg):
    feats, logF = spectrum_features(rgb)
    # demo calibration: camera-like photos ~0.45-0.6 hf_ratio and peaks < 2; upsampled synthetic ~0.34 with peaks > 5
    hf_score = float(np.clip((0.4 - feats["hf_ratio"]) / 0.3, 0, 1))
    peak_score = float(np.clip((feats["peak"] - 3.0) / 2.5, 0, 1)) if not is_jpeg else 0.0   # JPEG 8x8 blocks create peaks too
    raw = 0.4 * hf_score + 0.6 * peak_score
    vis = (logF - logF.min()) / (np.ptp(logF) + 1e-9)
    Image.fromarray(heat_colormap(vis)).save(os.path.join(out_dir, f"{key}_spectrum.png"))
    result = {"features": feats, "raw_score": round(float(raw), 2), "artifact": f"{key}_spectrum",
              "peak_checked": not is_jpeg}
    if raw >= 0.45:
        bits = []
        if hf_score > 0.3:
            bits.append(f"fine-detail energy is only {feats['hf_ratio']:.2f}x the mid-band level (camera-like photos in our tests: about 0.45-0.6)")
        if peak_score > 0.3:
            bits.append(f"isolated periodic peaks in the noise spectrum ({feats['peak']:.1f} log-units above their surroundings)")
        result["finding"] = finding(
            "image", "synthetic-generation", "Synthetic-like frequency pattern (EXPERIMENTAL)",
            "Experimental check: " + "; ".join(bits) + ". This pattern is typical of images built by upsampling, as AI generators do. "
            "It is NOT reliable on its own and is weighted low.",
            min(EXPERIMENTAL_CAP, 0.15 + 0.25 * raw), {"type": "file"},
            ["Image was resized/upscaled after capture", "Heavy noise reduction or smoothing", "Screenshot or rendered graphic"],
            artifact=f"{key}_spectrum")
        result["finding"]["experimental"] = True
    return result
