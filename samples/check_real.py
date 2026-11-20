"""End-to-end check on REAL files (run samples/fetch_real_samples.py first).
Uses a throw-away database, so the app's own data is untouched. Prints scores; there is no ground truth here,
so this is a behaviour check (no crashes, sensible outcomes), not an accuracy measurement."""
import os, sys, tempfile
TMP = tempfile.mkdtemp(prefix="truthlens_real_")
os.environ["TRUTHLENS_DB"] = os.path.join(TMP, "t.db")
os.environ["TRUTHLENS_DATA"] = os.path.join(TMP, "data")
os.environ["TRUTHLENS_REGISTRY"] = os.path.join(TMP, "reg")
os.environ.setdefault("TRUTHLENS_OFFLINE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from truthlens.app import app
c = TestClient(app)
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "real")
for s in ["original", "edited", "synthetic", "full"]:
    r = c.post(f"/api/demo?scenario={s}").json()
    pdf = c.get(f"/api/cases/{r['case_id']}/report.pdf")
    print(f"demo {s}: {r['overall']['score']:.2f} {r['overall']['band']}  pdf {pdf.status_code} {len(pdf.content)//1024} KB")
def up(names, cap=""):
    files = [("files", (n, open(os.path.join(R, n), "rb"))) for n in names]
    r = c.post("/api/analyze", files=files, data={"caption": cap, "title": "upload " + ",".join(names)})
    if r.status_code != 200:
        print("UPLOAD", names, r.status_code, r.text[:200]); return
    j = r.json(); pdf = c.get(f"/api/cases/{j['case_id']}/report.pdf"); js = c.get(f"/api/cases/{j['case_id']}/report.json")
    print(f"upload {names}: {j['overall']['score']:.2f} {j['overall']['band']} mods={[e['modality'] for e in j['evidence']]} corr={[x['title'][:50] for x in j['correlations']]} pdf {pdf.status_code} {len(pdf.content)//1024} KB json {js.status_code}")
up(["big_buck_bunny.mp4"], "LIVE unedited footage, one continuous take")
up(["Canon_40D.jpg", "vtest.avi", "198-209-0000.hq.ogg", "0_jackson_0.wav"], "Recorded 2008, real photo")
up(["CA.jpg"])
# register a real photo then upload a re-encoded copy and an edited copy
from PIL import Image, ImageDraw
import io
im = Image.open(os.path.join(R, "messi5.jpg")).convert("RGB")
reg = c.post("/api/registry", files=[("files", ("messi5.jpg", open(os.path.join(R, "messi5.jpg"), "rb")))], data={"source": "OpenCV samples"}).json()
print("registered", reg[0]["id"])
b = io.BytesIO(); im.save(b, "JPEG", quality=70); open(TMP + "/messi_reenc.jpg", "wb").write(b.getvalue())
e = im.copy(); ImageDraw.Draw(e).rectangle([200, 100, 300, 180], fill=(40, 120, 40)); b = io.BytesIO(); e.save(b, "JPEG", quality=90); open(TMP + "/messi_edit.jpg", "wb").write(b.getvalue())
for n in ["messi5.jpg"]:
    up([n])
R = TMP
up(["messi_reenc.jpg"]); up(["messi_edit.jpg"])
r = c.post("/api/analyze", files=[("files", ("evil.jpg", b"MZ\x90\x00 not an image"))]); print("bad upload", r.status_code, r.json()["detail"][:80])
print("custody", c.get("/api/custody/verify").json())
