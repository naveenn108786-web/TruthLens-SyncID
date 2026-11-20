"""Small source-labelled evaluation, NOT a benchmark. Run with --fetch on first use.

Downloads only example images, never model weights. Images stay under ignored samples/real/.
Sources/ground-truth labels are used ONLY by evaluation, never by the assessment.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCES = [
    {"name": f"sd-{n}.png", "ai": True,
     "url": f"https://raw.githubusercontent.com/CompVis/stable-diffusion/main/assets/stable-samples/txt2img/merged-{n}.png",
     "documentation": "https://github.com/CompVis/stable-diffusion#text-to-image-with-stable-diffusion",
     "label_basis": "Upstream text-to-image example montage, not a new independent generated sample per variant"}
    for n in ("0005", "0006", "0007")
] + [
    {"name": name, "ai": False,
     "url": f"https://raw.githubusercontent.com/ianare/exif-samples/master/jpg/{name}",
     "documentation": "https://github.com/ianare/exif-samples/blob/master/jpg/README",
     "label_basis": "Source-documented camera photograph; upstream resized in GIMP. No device-origin attestation"}
    for name in ("Nikon_D70.jpg", "Canon_40D.jpg", "Sony_HDR-HC3.jpg")
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--output", default=str(ROOT / "data" / "assessment-validation.json"))
    args = parser.parse_args()
    directory = ROOT / "samples" / "real" / "assessment-evaluation"
    directory.mkdir(parents=True, exist_ok=True)
    import httpx
    from PIL import Image, ImageDraw
    from PIL.PngImagePlugin import PngInfo
    if args.fetch:
        with httpx.Client(verify=ssl.create_default_context(), follow_redirects=True, timeout=90) as client:
            for source in SOURCES:
                path = directory / source["name"]
                if not path.exists():
                    response = client.get(source["url"])
                    response.raise_for_status()
                    path.write_bytes(response.content)
                    print("Downloaded", source["name"], flush=True)
    task_tmp = Path(tempfile.mkdtemp(prefix="truthlens-assessment-eval-"))
    os.environ["TRUTHLENS_DB"] = str(task_tmp / "truthlens.db")
    os.environ["TRUTHLENS_DATA"] = str(task_tmp / "cases")
    os.environ["TRUTHLENS_REGISTRY"] = str(task_tmp / "registry")
    os.environ["TRUTHLENS_OFFLINE"] = "1"
    from truthlens.pipeline import run_case
    rows = []
    def evaluate(source, group, content):
        report = run_case([(source["name"], content)], title="Assessment validation: " + group)
        ai = report["assessments"]["ai_generation"]
        positive = ai["status"] in ("evidence_detected", "possible")
        row = {"source": source["name"], "source_url": source["url"], "group": group, "source_label_ai": source["ai"],
               "sha256": hashlib.sha256(content).hexdigest(), "ai_status": ai["status"], "positive": positive,
               "manipulation_indicator": report["overall"]["score"], "methods": ai["methods"], "evidence": ai["evidence"],
               "case_id": report["case_id"]}
        rows.append(row)
        print(group, source["name"], ai["status"], "manipulation", row["manipulation_indicator"], flush=True)
    def encode(image, metadata=None):
        out = io.BytesIO()
        image.save(out, "PNG", pnginfo=metadata)
        return out.getvalue()
    for source in SOURCES:
        raw = (directory / source["name"]).read_bytes()
        source["download_sha256"] = hashlib.sha256(raw).hexdigest()
        evaluate(source, "ai_original" if source["ai"] else "camera_original", raw)
        with Image.open(io.BytesIO(raw)) as original:
            rgb = original.convert("RGB")
            stripped = Image.frombytes("RGB", rgb.size, rgb.tobytes())
            edit = stripped.copy()
            draw = ImageDraw.Draw(edit)
            draw.rectangle((edit.width // 3, edit.height // 3, edit.width // 2, edit.height // 2), fill=(255, 40, 40))
            evaluate(source, "ai_edited" if source["ai"] else "camera_edited", encode(edit))
            evaluate(source, "ai_metadata_removed" if source["ai"] else "camera_metadata_removed", encode(stripped))
            if source["ai"]:
                metadata = PngInfo()
                metadata.add_text("Software", "Stable Diffusion (evaluation-added truthful declaration)")
                evaluate(source, "ai_declared_control", encode(stripped, metadata))
            elif source["name"] == "Nikon_D70.jpg":
                metadata = PngInfo()
                metadata.add_text("Software", "Stable Diffusion (evaluation-added misleading declaration)")
                evaluate(source, "misleading_camera_metadata_control", encode(stripped, metadata))
    primary = [row for row in rows if row["group"] not in ("ai_declared_control", "misleading_camera_metadata_control")]
    metrics = {"true_positive": 0, "false_negative_or_abstention": 0, "false_positive": 0, "true_negative_or_abstention": 0}
    for row in primary:
        key = ("true_positive" if row["positive"] else "false_negative_or_abstention") if row["source_label_ai"] else ("false_positive" if row["positive"] else "true_negative_or_abstention")
        metrics[key] += 1
    result = {"scope": "Small source-labelled evaluation, 6 source files with correlated variants; not a benchmark or calibrated confidence",
              "positive_definition": "evidence_detected or possible; inconclusive/not_assessed are abstentions, not camera classifications",
              "edit_note": "Edited/metadata-removed variants are RGB PNG re-encodings. Changes include encoding as well as metadata; they are not isolated metadata-only experiments.",
              "limitations": "AI inputs are upstream example montages. Camera labels rely on source documentation, not independent capture attestation. Test controls add declarations explicitly; no detection results are fabricated.",
              "sources": SOURCES, "metrics": metrics, "abstentions": sum(r["ai_status"] in ("inconclusive", "not_assessed") for r in primary),
              "rows": rows, "temporary_case_directory": str(task_tmp)}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(metrics), "Abstentions:", result["abstentions"], flush=True)
    print("Results:", output, flush=True)


if __name__ == "__main__":
    main()
