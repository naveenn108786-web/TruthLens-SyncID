"""Download a small set of REAL (non-synthetic) public test files into samples/real/ (not committed).
Sources are public GitHub repositories used as test fixtures; check each repository's licence before redistributing.
Usage:  python samples/fetch_real_samples.py"""
import os
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "real")
FILES = {
    "Canon_40D.jpg": "https://raw.githubusercontent.com/ianare/exif-samples/master/jpg/Canon_40D.jpg",          # camera EXIF (edited in GIMP)
    "Sony_HDR-HC3.jpg": "https://raw.githubusercontent.com/ianare/exif-samples/master/jpg/Sony_HDR-HC3.jpg",
    "CA.jpg": "https://raw.githubusercontent.com/contentauth/c2pa-rs/main/sdk/tests/fixtures/CA.jpg",             # C2PA-signed
    "messi5.jpg": "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/messi5.jpg",                # photo, no EXIF
    "vtest.avi": "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/vtest.avi",                   # CCTV-style video, no audio
    "big_buck_bunny.mp4": "https://raw.githubusercontent.com/mediaelement/mediaelement-files/master/big_buck_bunny.mp4",  # produced video + audio
    "198-209-0000.hq.ogg": "https://raw.githubusercontent.com/librosa/data/main/audio/198-209-0000.hq.ogg",     # LibriSpeech read speech
    "0_jackson_0.wav": "https://raw.githubusercontent.com/Jakobovski/free-spoken-digit-dataset/master/recordings/0_jackson_0.wav",
}
_E = "https://raw.githubusercontent.com/ianare/exif-samples/master/jpg/gps/"
for _n in ("DSCN0010", "DSCN0012", "DSCN0021", "DSCN0025", "DSCN0027", "DSCN0029", "DSCN0038", "DSCN0040", "DSCN0042"):
    FILES[_n + ".jpg"] = _E + _n + ".jpg"                                                                     # Nikon COOLPIX photos with EXIF + GPS
FILES.update({
    "Megamind.avi": "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/Megamind.avi",            # movie trailer clip with audio
    "echo-hereweare.mp4": "https://raw.githubusercontent.com/mediaelement/mediaelement-files/master/echo-hereweare.mp4",
    "jfk.flac": "https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac",                            # real speech
    "pyannote_sample.wav": "https://raw.githubusercontent.com/pyannote/pyannote-audio/develop/tutorials/assets/sample.wav",
    "LDC93S1.wav": "https://raw.githubusercontent.com/mozilla/DeepSpeech/master/data/smoke_test/LDC93S1.wav",
})

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, url in FILES.items():
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            print("have", name); continue
        try:
            urllib.request.urlretrieve(url, p); print("got ", name)
        except Exception as ex:
            print("FAIL", name, ex)
