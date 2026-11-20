"""Controlled evaluation on REAL source files with KNOWN edits (run samples/fetch_real_samples.py first).

Every positive is made here from an independent real file (real camera photo, real speech, real video), so the label and
the location of the edit are known exactly. Clean controls are the untouched files and innocent re-saves.
This is NOT a benchmark: the edits are simple and made by us. It reports raw counts only, no accuracy percentage.

Usage:  python samples/eval_controlled.py [--keep]   (uses a throw-away database)
"""
import io
import os
import random
import subprocess
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="truthlens_eval_")
os.environ["TRUTHLENS_DB"] = os.path.join(TMP, "t.db")
os.environ["TRUTHLENS_DATA"] = os.path.join(TMP, "data")
os.environ["TRUTHLENS_REGISTRY"] = os.path.join(TMP, "reg")
os.environ.setdefault("TRUTHLENS_OFFLINE", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageFilter  # noqa: E402

from truthlens import registry  # noqa: E402
from truthlens.pipeline import run_case  # noqa: E402

REAL = os.path.join(ROOT, "samples", "real")
FLAG = 0.4            # a finding at or above this score counts as "flagged" (moderate or strong)
random.seed(7)


def jpeg_bytes(im, q=92, exif=None):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q, **({"exif": exif} if exif else {}))
    return b.getvalue()


def overlaps(loc, gt):
    if gt["type"] == "region" and loc.get("bbox"):
        ax, ay, aw, ah = loc["bbox"]
        bx, by, bw, bh = gt["bbox"]
        return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by)) > 0
    if gt["type"] == "time":
        if loc.get("type") == "segment":
            return loc["start"] - 1.0 <= gt["t"] <= loc["end"] + 1.0
        if loc.get("type") == "frame":
            return abs(loc["time"] - gt["t"]) <= 1.0
    return False


def run(name, files, caption=""):
    return run_case(files, caption, name)


def summarise(rep, gt=None):
    flagged = [(e["id"], f) for e in rep["evidence"] for f in e["findings"] if f["score"] >= FLAG]
    hit = [f for _, f in flagged if gt and overlaps(f["location"], gt)]
    return rep["overall"]["score"], flagged, hit


# ------------------------------------------------------------------ build cases
photos = sorted(f for f in os.listdir(REAL) if f.startswith("DSCN") and f.endswith(".jpg"))
cases = []      # (group, label, name, files, gt, caption)

for i, p in enumerate(photos):
    raw = open(os.path.join(REAL, p), "rb").read()
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    exif = Image.open(io.BytesIO(raw)).info.get("exif")
    W, H = im.size
    cases.append(("image", "clean", f"{p} untouched", [(p, raw)], None, ""))
    cases.append(("image", "clean", f"{p} re-saved q75", [(p, jpeg_bytes(im, 75, exif))], None, ""))
    s = 140
    x, y = random.randint(40, W - s - 40), random.randint(40, H - s - 40)
    kind = ["splice", "copy-move", "retouch-blur"][i % 3]
    ed = im.copy()
    if kind == "splice":
        donor = Image.open(os.path.join(REAL, photos[(i + 4) % len(photos)])).convert("RGB")
        ed.paste(donor.crop((100, 100, 100 + s, 100 + s)), (x, y))
    elif kind == "copy-move":
        sx, sy = (x + W // 2) % (W - s), (y + H // 3) % (H - s)
        ed.paste(im.crop((sx, sy, sx + s, sy + s)), (x, y))
    else:
        ed.paste(im.crop((x, y, x + s, y + s)).filter(ImageFilter.GaussianBlur(4)), (x, y))
    gt = {"type": "region", "bbox": [x, y, s, s]}
    cases.append(("image", f"edited:{kind}", f"{p} {kind} (unregistered)", [(p, jpeg_bytes(ed, 92, exif))], gt, ""))
    cases.append(("image-registered", f"edited:{kind}", f"{p} {kind} (original registered)", [(p, jpeg_bytes(ed, 92, exif))], gt, "REG:" + p))
    cases.append(("image-registered", "clean", f"{p} re-saved q75 (original registered)", [(p, jpeg_bytes(im, 75, exif))], None, "REG:" + p))


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def wav(path, out, sr=16000):
    ff("-i", path, "-ac", "1", "-ar", str(sr), out)


speech = [f for f in ("jfk.flac", "pyannote_sample.wav", "198-209-0000.hq.ogg", "LDC93S1.wav") if os.path.exists(os.path.join(REAL, f))]
A = os.path.join(TMP, "audio")
os.makedirs(A, exist_ok=True)
for i, f in enumerate(speech):
    src = os.path.join(A, f"{i}.wav")
    wav(os.path.join(REAL, f), src)
    cases.append(("audio", "clean", f"{f} untouched", [(f, open(os.path.join(REAL, f), "rb").read())], None, ""))
    mp3 = os.path.join(A, f"{i}.mp3")
    ff("-i", src, "-b:a", "64k", mp3)
    cases.append(("audio", "clean", f"{f} re-encoded mp3", [(f + ".mp3", open(mp3, "rb").read())], None, ""))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src], capture_output=True, text=True).stdout)
    if dur < 6:
        continue
    other = os.path.join(A, f"o{i}.wav")
    wav(os.path.join(REAL, speech[(i + 1) % len(speech)]), other)
    t = round(dur / 2, 2)
    out = os.path.join(A, f"splice{i}.wav")
    ff("-i", src, "-i", other, "-filter_complex",
       f"[0]atrim=0:{t},asetpts=N/SR/TB[a];[1]atrim=1:3.5,asetpts=N/SR/TB[b];[0]atrim={t},asetpts=N/SR/TB[c];[a][b][c]concat=n=3:v=0:a=1", out)
    cases.append(("audio", "edited:splice-other-recording", f"{f} + 2.5s of another recording at {t}s", [(f"spliced_{i}.wav", open(out, "rb").read())],
                  {"type": "time", "t": t}, ""))

V = os.path.join(TMP, "video")
os.makedirs(V, exist_ok=True)
videos = [f for f in ("vtest.avi", "Megamind.avi", "echo-hereweare.mp4") if os.path.exists(os.path.join(REAL, f))]
for i, f in enumerate(videos):
    src = os.path.join(REAL, f)
    cases.append(("video", "clean", f"{f} untouched", [(f, open(src, "rb").read())], None, ""))
    re = os.path.join(V, f"re{i}.mp4")
    ff("-i", src, "-t", "20", "-c:v", "libx264", "-crf", "28", "-c:a", "aac", re)
    cases.append(("video", "clean", f"{f} re-encoded", [(f"re_{i}.mp4", open(re, "rb").read())], None, ""))
    other = os.path.join(REAL, videos[(i + 1) % len(videos)])
    t = 4.0
    ins = os.path.join(V, f"ins{i}.mp4")
    ff("-i", src, "-i", other, "-filter_complex",
       "[0:v]trim=0:4,setpts=PTS-STARTPTS,scale=640:480,setsar=1,fps=25[a];[1:v]trim=2:4,setpts=PTS-STARTPTS,scale=640:480,setsar=1,fps=25[b];"
       "[0:v]trim=4:10,setpts=PTS-STARTPTS,scale=640:480,setsar=1,fps=25[c];[a][b][c]concat=n=3:v=1:a=0", "-an", "-c:v", "libx264", "-crf", "18", ins)
    cases.append(("video", "edited:inserted-segment", f"{f} with 2s from another video at 4s", [(f"inserted_{i}.mp4", open(ins, "rb").read())],
                  {"type": "time", "t": t}, ""))

# text / cross-modal: caption date vs a real photo's EXIF date (2008-11-01)
p = os.path.join(REAL, photos[0])
cases.append(("cross-modal", "edited:date-contradiction", "photo + caption claiming it was taken yesterday",
              [(photos[0], open(p, "rb").read())], {"type": "corr"}, "Photo taken yesterday at the protest."))
cases.append(("cross-modal", "clean", "photo + caption with the correct date",
              [(photos[0], open(p, "rb").read())], None, "Photo taken on 22 October 2008."))

# ------------------------------------------------------------------ run
rows = []
for group, label, name, files, gt, caption in cases:
    if caption.startswith("REG:"):
        registry.reset_to_seed() if hasattr(registry, "reset_to_seed") else None
        orig = caption[4:]
        registry.add(orig, open(os.path.join(REAL, orig), "rb").read(), "eval", "")
        caption = ""
    try:
        rep = run(name, files, caption)
    except Exception as ex:      # a crash is a result too
        rows.append((group, label, name, "CRASH", f"{type(ex).__name__}: {ex}", ""))
        continue
    errors = [l for l in rep["log"] if l["step"] == "analysis.error"]
    score, flagged, hit = summarise(rep, gt)
    if gt and gt["type"] == "corr":
        ok = any(c["effect"] == "strengthens" and "date" in c["title"].lower() for c in rep["correlations"])
        outcome = "detected" if ok else "MISSED"
    elif gt:
        outcome = "detected+located" if hit else ("flagged, wrong place" if flagged else "MISSED")
    else:
        bad = [c for c in rep["correlations"] if c["effect"] == "strengthens"]
        outcome = "FALSE ALARM" if (flagged or bad) else "clean"
    why = "; ".join(sorted({f["indicator"] for _, f in flagged}))[:110]
    rows.append((group, label, name, outcome, f"{score:.2f}", why + (" [analysis.error]" if errors else "")))

print(f"{'group':17} {'label':30} {'outcome':22} {'score':5}  case / flagged indicators")
for g, l, n, o, s, w in rows:
    print(f"{g:17} {l:30} {o:22} {s:5}  {n}" + (f"  <- {w}" if w else ""))
print()
from collections import Counter
for g in sorted({r[0] for r in rows}):
    c = Counter(r[3] for r in rows if r[0] == g)
    print(g, dict(c))
