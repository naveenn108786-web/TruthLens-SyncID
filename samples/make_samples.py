"""Generate the TruthLens demo evidence set (synthetic, fully reproducible).

Demo story: a "viral clip" of a fictional politician at a rally.
  - rally_original.jpg   untouched camera photo (EXIF from 2019, natural sensor noise)
  - rally_edited.jpg     same photo with the face region replaced (smooth, no sensor noise,
                         different compression history) and re-saved in Photoshop
  - viral_clip.mp4       video with a face region lacking sensor noise, an inserted segment
                         at 4.0-5.0 s, and a spliced audio track
  - speech_spliced.wav   the audio on its own: natural "speech" with a synthetic,
                         studio-clean segment spliced in at 4.0-5.6 s
  - caption.txt          the claim that went viral with the clip (contradicts the evidence)
  - ai_portrait.png      a synthetic-looking portrait (upsampled, checkerboard artefacts, no metadata)
  - ../registry/         "registered originals": rally_original.jpg (with C2PA credentials) + 2 others

All people, names and events are fictional. Run:  python samples/make_samples.py
"""
import io
import json
import os
import shutil
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ExifTags

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo")
REG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry")   # demo "registered originals"
rng = np.random.default_rng(7)


# ------------------------------------------------------------------ scene

def render_scene(w, h, t=0.0, face_variant=0, shift=0, sky=(110, 160, 220)):
    """Draw a simple rally scene with PIL. Returns RGB uint8 (clean, before sensor noise)."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    s = w / 1280
    for y in range(int(h * 0.55)):                      # sky gradient
        k = y / (h * 0.55)
        d.line([(0, y), (w, y)], fill=tuple(int(c * (1 - 0.35 * k) + 40 * k) for c in sky))
    d.rectangle([0, int(h * 0.55), w, h], fill=(70, 95, 60))       # ground
    for i in range(14):                                  # crowd heads
        cx = int((i * 97 + shift * 0.5) % w)
        d.ellipse([cx - 28 * s, h * 0.80, cx + 28 * s, h * 0.80 + 60 * s], fill=(40 + i * 7 % 50, 30, 25))
    bx = int(w * 0.18 + shift)                           # banner
    d.rectangle([bx, h * 0.12, bx + 520 * s, h * 0.24], fill=(200, 30, 40))
    d.text((bx + 30 * s, h * 0.15), "PEOPLE'S RALLY  -  JOBS FOR ALL", fill=(255, 255, 255))
    px = int(w * 0.5 + shift)                            # podium + speaker
    d.rectangle([px - 120 * s, h * 0.60, px + 120 * s, h * 0.95], fill=(120, 80, 45))
    d.rectangle([px - 70 * s, h * 0.38, px + 70 * s, h * 0.62], fill=(30, 40, 90))    # suit
    fx, fy, fr = px, int(h * 0.30), int(55 * s)
    skin = (205, 160, 130) if face_variant == 0 else (215, 172, 140)
    d.ellipse([fx - fr, fy - fr * 1.2, fx + fr, fy + fr * 1.2], fill=skin)
    d.ellipse([fx - fr, fy - fr * 1.3, fx + fr, fy - fr * 0.5], fill=(40, 30, 25))   # hair
    ey = fy - fr * 0.15
    for ex in (fx - fr * 0.4, fx + fr * 0.4):
        d.ellipse([ex - 7 * s, ey - 5 * s, ex + 7 * s, ey + 5 * s], fill=(30, 30, 30))
    mouth_open = 4 + 8 * abs(np.sin(t * 9)) if face_variant == 0 else 3 + 10 * abs(np.sin(t * 7))
    d.ellipse([fx - 18 * s, fy + fr * 0.45, fx + 18 * s, fy + fr * 0.45 + mouth_open * s], fill=(120, 40, 40))
    img = img.filter(ImageFilter.GaussianBlur(1.4))      # lens softness
    face_box = (fx - fr - 10, int(fy - fr * 1.35), 2 * fr + 20, int(fr * 2.7))
    return np.asarray(img), face_box


def add_sensor_noise(rgb, sigma=6.0):
    n = rng.normal(0, sigma, rgb.shape)
    return np.clip(rgb.astype(np.float64) + n, 0, 255).astype(np.uint8)


def exif_bytes(make, model, original, modified, software=None):
    ex = Image.Exif()
    if make:
        ex[ExifTags.Base.Make] = make
        ex[ExifTags.Base.Model] = model
    ex[ExifTags.Base.DateTime] = modified
    if software:
        ex[ExifTags.Base.Software] = software
    ex.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = original
    return ex.tobytes()


# ------------------------------------------------------------------ images

def sign_c2pa(src_path, dst_path, title):
    """Embed C2PA content credentials signed with a throwaway demo certificate.
    Returns False (and copies the file unsigned) if openssl or c2pa-python is unavailable."""
    import shutil
    try:
        import c2pa
        with tempfile.TemporaryDirectory() as d:
            cnf = os.path.join(d, "ca.cnf")
            with open(cnf, "w") as f:
                f.write("[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=TruthLens Demo Root CA\nO=TruthLens Demo\n"
                        "[v3_ca]\nbasicConstraints=critical,CA:true\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n"
                        "[v3_leaf]\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=emailProtection\n"
                        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n")
            run = lambda *a: subprocess.run(list(a), cwd=d, check=True, capture_output=True)
            run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key")
            run("openssl", "req", "-x509", "-new", "-key", "ca.key", "-days", "3650", "-config", cnf, "-extensions", "v3_ca", "-out", "ca.pem")
            run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "leaf.key")
            run("openssl", "pkcs8", "-topk8", "-nocrypt", "-in", "leaf.key", "-out", "leaf8.key")
            run("openssl", "req", "-new", "-key", "leaf.key", "-subj", "/CN=Demo Newsroom Camera App/O=Demo Newsroom", "-out", "leaf.csr")
            run("openssl", "x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-days", "3650",
                "-extfile", cnf, "-extensions", "v3_leaf", "-out", "leaf.pem")
            chain = open(os.path.join(d, "leaf.pem"), "rb").read() + open(os.path.join(d, "ca.pem"), "rb").read()
            signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg=b"es256", sign_cert=chain,
                                                                private_key=open(os.path.join(d, "leaf8.key"), "rb").read(), ta_url=None))
            manifest = {"claim_generator_info": [{"name": "Demo Newsroom Camera", "version": "1.0"}], "title": title,
                        "assertions": [{"label": "c2pa.actions", "data": {"actions": [{"action": "c2pa.created",
                                        "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"}]}}]}
            with c2pa.Builder(manifest) as b, open(src_path, "rb") as src, open(dst_path, "wb") as dst:
                b.sign(signer, "image/jpeg", src, dst)
        return True
    except Exception as ex:
        print("C2PA signing skipped:", ex)
        shutil.copy(src_path, dst_path)
        return False


def make_images():
    clean, face = render_scene(1280, 720)
    original = add_sensor_noise(clean)
    tmp = os.path.join(OUT, "_unsigned.jpg")
    Image.fromarray(original).save(tmp, "JPEG", quality=92,
                                   exif=exif_bytes("Canon", "EOS 90D", "2019:03:14 17:42:10", "2019:03:14 17:42:10"))
    sign_c2pa(tmp, os.path.join(OUT, "rally_original.jpg"), "rally_original.jpg")
    os.remove(tmp)

    # Tampered: swap in a different face rendered without sensor noise, pre-compressed at low quality
    swapped, _ = render_scene(1280, 720, t=0.4, face_variant=1)
    x, y, w, h = face
    patch = Image.fromarray(swapped[y:y + h, x:x + w]).filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    patch.save(buf, "JPEG", quality=60)
    patch = np.asarray(Image.open(buf).convert("RGB"))
    edited = original.copy()
    edited[y:y + h, x:x + w] = patch
    Image.fromarray(edited).save(os.path.join(OUT, "rally_edited.jpg"), "JPEG", quality=92,
                                 exif=exif_bytes("Canon", "EOS 90D", "2019:03:14 17:42:10", "2026:11:18 23:05:44",
                                                 software="Adobe Photoshop 25.1 (Windows)"))
    return face


# ------------------------------------------------------------------ audio

SR = 16000


def syllable(dur, f0, jitter, formants, tilt):
    n = int(dur * SR)
    t = np.arange(n) / SR
    if jitter > 0:   # natural voice: cycle-to-cycle pitch wobble + slow drift
        wobble = np.convolve(rng.normal(0, 1, n), np.ones(40) / np.sqrt(40), mode="same")
        f = f0 * (1 + jitter * wobble + 0.04 * np.sin(2 * np.pi * 3 * t))
    else:            # synthetic voice: perfectly steady pitch with a slow linear glide
        f = f0 * (1 + 0.03 * t / max(dur, 1e-3))
    phase = 2 * np.pi * np.cumsum(f) / SR
    sig = np.zeros(n)
    for k in range(1, 30):
        fk = f0 * k
        amp = sum(np.exp(-((fk - F) ** 2) / (2 * 120 ** 2)) for F in formants) + 0.05
        sig += amp * k ** (-tilt) * np.sin(k * phase)
    env = np.sin(np.pi * np.linspace(0, 1, n)) ** 0.6
    return sig * env


VOWELS = [(700, 1200), (400, 2000), (300, 2300), (500, 900), (600, 1700)]


def speech(dur, f0, jitter, tilt, gap_noise):
    out = []
    total = 0
    while total < dur * SR:
        d = rng.uniform(0.14, 0.26)
        s = syllable(d, f0 * rng.uniform(0.9, 1.1), jitter, VOWELS[rng.integers(len(VOWELS))], tilt)
        g = np.zeros(int(rng.uniform(0.04, 0.15) * SR))
        out += [s, g]
        total += len(s) + len(g)
    x = np.concatenate(out)[: int(dur * SR)]
    x = x / (np.abs(x).max() + 1e-9) * 0.5
    return x


def make_audio(path, dur=8.0, splice=(4.0, 5.6)):
    natural = speech(dur, 120, jitter=0.02, tilt=1.0, gap_noise=True)
    # room tone + light reverb so the natural part sounds like a real recording
    room = np.convolve(rng.normal(0, 1, len(natural)), np.ones(8) / 8, mode="same") * 0.02
    rev = np.convolve(natural, np.exp(-np.arange(int(0.08 * SR)) / (0.02 * SR)) * 0.25, mode="full")[: len(natural)]
    natural = natural + rev + room
    # synthetic, studio-clean segment: flat pitch, no jitter, no room noise, digital silence
    a, b = int(splice[0] * SR), int(splice[1] * SR)
    synth = speech((b - a) / SR, 140, jitter=0.0, tilt=0.6, gap_noise=False) * 0.9
    x = natural.copy()
    x[a:b] = synth
    x = np.clip(x, -1, 1)
    from scipy.io import wavfile
    wavfile.write(path, SR, (x * 32767).astype(np.int16))


# ------------------------------------------------------------------ video

def make_video(path, audio_path, dur=8.0, fps=25, insert=(4.0, 5.0)):
    w, h = 640, 360
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "v.mp4")
        vw = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for i in range(int(dur * fps)):
            t = i / fps
            inserted = insert[0] <= t < insert[1]
            if inserted:   # inserted segment: different camera angle / lighting
                frame, face = render_scene(w, h, t=t, shift=-90 + 6 * t, sky=(150, 150, 170))
            else:
                frame, face = render_scene(w, h, t=t, shift=8 * np.sin(t))
            noisy = add_sensor_noise(frame, 9.0)
            x, y, fw, fh = face          # deepfake-style face: no sensor noise, slightly blurred
            noisy[y:y + fh, x:x + fw] = cv2.GaussianBlur(frame[y:y + fh, x:x + fw], (5, 5), 0)
            vw.write(cv2.cvtColor(noisy, cv2.COLOR_RGB2BGR))
        vw.release()
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-i", audio_path,
                        "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-metadata", "creation_time=2019-03-14T17:45:00Z", "-shortest", path], check=True)


def make_registry_extras():
    """Two more 'registered originals' so the registry is not a single-item list."""
    for name, sky, shift, date in [("press_conference.jpg", (180, 190, 205), -200, "2025:08:02 11:15:03"),
                                   ("flood_relief_camp.jpg", (150, 165, 185), 260, "2024:07:21 08:02:44")]:
        clean, _ = render_scene(1280, 720, shift=shift, sky=sky, face_variant=1)
        Image.fromarray(add_sensor_noise(clean)).save(os.path.join(REG, name), "JPEG", quality=92,
                                                      exif=exif_bytes("Nikon", "Z6 II", date, date))


def make_synthetic():
    """A 'too clean' close-up portrait built like a generator would: low-res render, 2x upsampling,
    and the faint checkerboard that transposed convolutions leave behind. No metadata."""
    img = Image.new("RGB", (384, 384))
    d = ImageDraw.Draw(img)
    for y in range(384):                                       # studio backdrop gradient
        d.line([(0, y), (384, y)], fill=(60 + y // 6, 70 + y // 8, 110 + y // 5))
    d.rectangle([92, 300, 292, 384], fill=(35, 45, 80))         # shoulders
    d.ellipse([122, 70, 262, 250], fill=(214, 170, 140))       # face
    d.ellipse([112, 50, 272, 140], fill=(45, 32, 28))          # hair
    for ex in (160, 224):
        d.ellipse([ex - 12, 150, ex + 12, 166], fill=(255, 255, 255))
        d.ellipse([ex - 6, 152, ex + 6, 164], fill=(40, 70, 60))
    d.polygon([(192, 165), (182, 205), (202, 205)], fill=(195, 150, 120))
    d.chord([160, 205, 224, 240], 0, 180, fill=(170, 60, 70))
    clean = np.asarray(img.filter(ImageFilter.GaussianBlur(2.0)))
    up = cv2.resize(clean, (768, 768), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    cb = ((np.indices((768, 768)).sum(0) % 2) * 2 - 1)[..., None] * 1.5
    Image.fromarray(np.clip(up + cb, 0, 255).astype(np.uint8)).save(os.path.join(OUT, "ai_portrait.png"))


CAPTION = ("BREAKING: Unedited LIVE footage recorded TODAY (19 Nov 2026) at the Bengaluru rally shows "
           "Minister R. Kumar admitting he took the bribe. Nothing has been edited, 100% real. "
           "Share before it gets deleted!!!")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(REG, exist_ok=True)
    make_images()
    shutil.copy(os.path.join(OUT, "rally_original.jpg"), os.path.join(REG, "rally_original.jpg"))
    make_registry_extras()
    with open(os.path.join(REG, "sources.json"), "w") as f:
        json.dump({"rally_original.jpg": {"source": "Demo Newsroom photo desk", "note": "Staff photo, People's Rally, 14 Mar 2019"},
                   "press_conference.jpg": {"source": "Demo Newsroom photo desk", "note": "Press conference, Aug 2025"},
                   "flood_relief_camp.jpg": {"source": "Demo State Info Dept", "note": "Relief camp, Jul 2024"}}, f, indent=2)
    make_synthetic()
    wav = os.path.join(OUT, "speech_spliced.wav")
    make_audio(wav)
    make_video(os.path.join(OUT, "viral_clip.mp4"), wav)
    with open(os.path.join(OUT, "caption.txt"), "w") as f:
        f.write(CAPTION + "\n")
    print("Demo evidence written to", OUT)
    for n in sorted(os.listdir(OUT)):
        print(" ", n, os.path.getsize(os.path.join(OUT, n)), "bytes")
