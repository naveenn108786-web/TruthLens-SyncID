"""Forensic analyzers. Each analyzer returns a dict:
{
  "modality": "image" | "video" | "audio" | "text",
  "metadata": {...},          # facts extracted from the file (EXIF, duration, ...)
  "findings": [Finding, ...], # flagged indicators, see common.finding()
  "artifacts": {name: relative_path},  # heatmaps, keyframes, spectrograms
  "limitations": [str, ...],  # what this analysis cannot tell you
}
"""
