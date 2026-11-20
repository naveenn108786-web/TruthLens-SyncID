# TruthLens: Don't Just Detect the Fake. Investigate Its Origin.

## Sync ID hackathon prototype

An independent UUIDv7 / SHA-256 / Ed25519 registration service and explicit TruthLens verification panel are now included.
Start `run-syncid.bat`, start TruthLens normally, then choose **Sync ID** in its toolbar.
See [Sync ID run instructions, demo, test results and limitations](syncid/README.md).


Problem Statement 04, Innovedge / SAPTHA Hackathon 2026. Demo prototype.

TruthLens investigates **where a piece of media came from and what happened to it**. It does not just
output a fake-or-real score. Two engines feed one explainable report:

1. **Authenticity & Provenance Engine**
   - SHA-256 exact matching and perceptual-hash (pHash) similarity against a registry of known originals
   - metadata forensics
   - C2PA content-credential verification (official `c2pa-python` SDK)
   - file-hash history across earlier cases
   - chain-of-custody log
2. **Manipulation Detection Engine**
   - changed-region highlighting
   - visual diff against the registered original
   - camera sensor-noise consistency against that original
   - metadata inconsistencies
   - local noise and ELA checks
   - **experimental** generator-fingerprint (frequency-domain) analysis
3. **Evidence correlation & forensic report**
   - an Investigation → Finding table
   - calibrated confidence shown as a range
   - "What this cannot establish"
   - PDF and JSON export

The video, audio and caption checks remain as supporting multimodal evidence.

> **Hard rule:** an image is never labelled fake because it is unregistered, has no metadata or has no C2PA
> credentials. These are reported as neutral information with a score of 0. Detection results are indicators, not proof.

## Repository guide

This repository contains two cooperating services:

| Part | What it does | Folder | Port |
|---|---|---|---|
| **TruthLens** | Forensic investigation of image, video, audio and text: provenance, manipulation indicators, cross-modal correlation, PDF/JSON report | `truthlens/` | 8000 |
| **Sync ID** | Independent content-identity service: UUIDv7 + SHA-256 + Ed25519-signed registration records that anyone can verify | `syncid/` | 8001 |

### Key features
- Registry of known originals with SHA-256 exact match and perceptual-hash (pHash) near-match
- C2PA content-credential verification, metadata forensics, hash-chained chain-of-custody log
- Visual diff and sensor-noise comparison against a registered original; noise/ELA checks
- Video cut and per-frame noise tracking, audio splice segments, caption claim extraction
- Cross-modal correlation into an Investigation → Finding table, exported as PDF and JSON
- Experimental (never score-changing) generator-fingerprint and people/face analysis
- Sync ID: signed, publicly verifiable identity records that TruthLens can register and verify
- Works fully offline without an API key; an optional Claude review is used only if `ANTHROPIC_API_KEY` is set

### Technology stack
Python 3.10+, FastAPI + Uvicorn, NumPy, SciPy, Pillow, OpenCV (incl. YuNet face model, BSD-3, `truthlens/forensics/models/`),
ReportLab (PDF), c2pa-python, cryptography (Ed25519), uuid6, RFC 8785 canonical JSON, SQLite, FFmpeg/FFprobe,
vanilla HTML/CSS/JS frontend (`truthlens/static/`), pytest.

### Sync ID architecture
```
producer / TruthLens  --HTTP-->  syncid/app.py (FastAPI)
                                   ├─ engine.py   UUIDv7 id, SHA-256 content hash, canonical (RFC 8785) record
                                   └─ crypto.py   Ed25519 signing key in ~/.syncid/ (created on first run, never committed)
TruthLens: truthlens/syncid_client.py + static/syncid.js (Sync ID panel)
```
Full details: [syncid/README.md](syncid/README.md).

### Installation
```bash
git clone https://github.com/naveenn108786-web/TruthLens-SyncID.git
cd TruthLens-SyncID
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```
Install FFmpeg and FFprobe and make sure both are on PATH (Windows: `winget install --id Gyan.FFmpeg`).

### How to run Sync ID
```bash
python -m uvicorn syncid.app:app --host 127.0.0.1 --port 8001   # or run-syncid.bat on Windows
```
API docs: http://127.0.0.1:8001/docs

### How to run TruthLens
```bash
./run.sh            # Windows: run.bat
```
Open http://127.0.0.1:8000. Start Sync ID first if you want to use the **Sync ID** toolbar panel.

### API documentation
Interactive OpenAPI docs are served at `/docs` on both services.

TruthLens (port 8000):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Service and analyser status |
| POST | `/api/analyze` | Upload media (image/video/audio/text) and run an investigation |
| POST | `/api/demo` | Run a bundled demo case |
| GET/POST | `/api/registry` | List / register known originals |
| POST | `/api/registry/reset` | Reset the registry to the demo originals |
| GET | `/api/custody/verify` | Verify the hash-chained custody log |
| GET | `/api/cases`, `/api/cases/{cid}` | Case history and a case's results |
| GET | `/api/cases/{cid}/report.pdf`, `/report.json` | Forensic report export |
| POST | `/api/syncid/register`, `/api/syncid/verify` | Proxy to Sync ID |

Sync ID (port 8001): `GET /health`, `GET /v1/public-key`, `POST /v1/register`, `GET /v1/identity/{sync_id}`, `POST /v1/verify`.
Request/response formats are in [syncid/README.md](syncid/README.md#api).

### Demo instructions
1. Start Sync ID, then TruthLens. 2. Open http://127.0.0.1:8000 and pick a demo case (table below).
3. Export the PDF report. 4. Optional producer demo: `python -m syncid.demo --truthlens-url http://127.0.0.1:8000`.
The full presenter script is in [docs/DEMO.md](docs/DEMO.md).

### Tests
```bash
python -m pytest -q tests syncid/tests
```

### Known limitations
See [Limitations](#limitations) below, [docs/QA.md](docs/QA.md) and [syncid/README.md](syncid/README.md#deliberately-incomplete).
In short: heuristic forensics, not trained deepfake classifiers; blind detection without a registered original is weak;
AI-generation assessment can be inconclusive; Sync ID is a prototype of a proposed protocol, not an adopted standard.

## Run it

```bash
pip install -r requirements.txt      # also needs ffmpeg (and openssl to regenerate the C2PA demo file)
./run.sh                             # open http://localhost:8000 and pick a demo case
```

### Windows (PowerShell or VS Code terminal)

```powershell
winget install --id Gyan.FFmpeg          # once; then close and reopen the terminal so ffmpeg is on PATH
git clone https://github.com/naveenn108786-web/TruthLens-SyncID.git
cd TruthLens-SyncID
python -m venv .venv
.\.venv\Scripts\Activate.ps1           # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
.\run.bat                               # open http://localhost:8000
```
Use Python 3.10 or newer (64-bit). The demo samples are already in the repo, so OpenSSL is not needed.
Check the installs with `ffmpeg -version` and `python --version`. Run the tests with `python -m pytest -q tests`.

To turn on the AI claims check and summary, which is optional, run `export ANTHROPIC_API_KEY=...` before starting.
Without a key, everything still works using the rule-based analysis.

## Demo cases (sidebar)

| Demo | What it shows | Indicator |
|---|---|---|
| Verified original | SHA-256 exact match to a registered original, with intact C2PA credentials (signed with a demo certificate, so the signer is not trusted) | 0.11 (low) |
| Edited copy of a registered photo | pHash match plus SHA-256 mismatch. The visual diff and sensor-noise comparison pinpoint the swapped face, and the metadata shows a Photoshop re-save | 0.74 (high) |
| Unregistered synthetic-looking portrait | Not in the registry and no metadata (both neutral). The experimental frequency analysis and a noise-free image agree, so the case goes to human review | 0.39 (review) |
| Viral rally post | Photo, clip and caption together: date contradictions, an inserted segment, and an audio splice aligned with a video cut | 0.95 (capped) |

The registry starts with three demo originals from `samples/registry/`. You can register your own originals in the sidebar.

## What it checks

| Media | Technique | What it catches |
|---|---|---|
| Image | Noise residual consistency (16 px blocks, robust z-scores) | Smoothed, pasted or AI-rendered regions that lack camera sensor noise |
| Image | Error Level Analysis, normalised by texture | Areas with a different JPEG compression history |
| Image | EXIF / PNG metadata | Editing software, AI-generator tags, capture vs modified date. Missing metadata is reported as neutral |
| Image | Registry: SHA-256 + 64-bit DCT pHash | Exact copy, re-encoded copy or modified copy of a registered original |
| Image | Visual diff + block-wise noise-residual correlation vs the original | Which area changed, and whether the rest still carries the original camera's noise pattern |
| Image | C2PA manifest validation | Content changed after signing (integrity), plus the claimed signer, recorded actions and any AI-generated declaration. Signer trust is reported separately; no trust list is configured |
| Image | *Experimental*: spectrum of the noise residual | Upsampling peaks and a high-frequency deficit typical of generators. Capped at 0.35 and never a verdict on its own |
| Video | Frame-to-frame difference spikes | Hard cuts and inserted segments in "continuous" footage |
| Video | Noise check on sampled frames, tracked across time | Face-swap or inpainted region that persists across frames |
| Video | ffprobe container metadata | Creation time, encoder |
| Audio | Noise floor, digital silence, spectral centroid and pitch jitter per 0.5 s window | Spliced segments and unnaturally steady (synthetic) voice, with timestamps |
| Text | Claim extraction (dates, "unedited", "live", places) plus urgency framing; optional Claude reading | Claims that can be checked against the media |
| Cross-modal | Rule-based correlation | Caption date vs EXIF/creation date, "unedited" claim vs edits, "live" vs cuts, audio splice aligned with video cut, near-duplicate image (finds the original and the edited area) |

## Scoring (how confidence works)

- Each finding has a heuristic strength from 0 to 1. Weak signals with many innocent causes are capped low.
- Each evidence item's score combines its findings with a noisy-OR, and weak findings are discounted.
- The overall score is led by the most suspicious item. Other items and cross-modal links add weight, and the total is capped at 0.95.
- The **range** is wider when evidence is thin (a single media type, no metadata) and narrower when independent modalities agree.
- The scores are hand-tuned, not benchmark-calibrated, and the report says so.

## Foundation: database, validation, custody (Phase 1)

- **SQLite** (`data/truthlens.db`, `truthlens/db.py`) has four tables: `investigations`, `evidence`, `registry` and `custody_log`.
  Every investigation, evidence item and registered original gets a **UUID**.
- **Upload validation** (`truthlens/validation.py`) identifies each file by its content (magic bytes), not its name.
  - Images must decode, with a decompression-bomb pixel limit.
  - Video and audio must contain a matching stream according to ffprobe.
  - Text must be UTF-8.
  - There are size and count limits.
  - A rejected upload stores nothing. A file whose extension doesn't match its content is accepted, and the mismatch is recorded.
- The **tamper-evident custody log** is a hash chain. Each entry stores the previous entry's SHA-256, and its own hash covers its
  content plus that previous hash. `GET /api/custody/verify` recomputes the whole chain.
  - **Tested and detected:** an edited entry, and an entry deleted from the middle of the chain (reported at that position).
  - **Not detected:** deletion of the newest entry, because the head hash is not anchored outside the database.
  - **Not tested:** re-ordering and inserted entries (see [docs/QA.md](docs/QA.md)).

  The report file's own SHA-256 is logged when it is generated and re-checked when it is opened.
  The chain is tamper-*evident*, not tamper-proof: someone with write access could rebuild it, unless the head hash is anchored outside the database.

## Tests

```bash
python -m pytest -q tests      # 36 tests, ~40 s, offline
```
The suite covers validation, the custody chain (including detecting tampering), registry exact and perceptual matching, the hard rule
that missing registry, metadata or C2PA data is never an indicator, coverage of all four media types, upload rejection and path traversal.
The pipeline tests are **regression tests on the synthetic demo set**. They check behaviour and rules, not accuracy.

## Check on real files

```bash
python samples/fetch_real_samples.py   # downloads public test files into samples/real/ (gitignored)
python samples/check_real.py           # runs them end to end on a throw-away database
```
The files are real camera JPEGs with EXIF, a C2PA-signed image, a CCTV-style video, a produced video with a soundtrack
and real speech recordings. They have no ground-truth labels, so this is a behaviour check (no crashes, sensible outcomes),
not an accuracy measurement. It found one crash and three false-alarm patterns, which are fixed (see git history).

### Controlled evaluation (real files, known edits)

```bash
python samples/eval_controlled.py
```
We take independent real files (9 Nikon camera photos, 4 speech recordings, 3 videos) and make known edits ourselves:
splice, copy-move and blur retouch on photos; 2.5 s of another recording spliced into speech; 2 s of another video inserted.
Clean controls are the untouched files and innocent re-saves. Raw counts from the latest run (19 Nov), **not a benchmark**:

| Group | Edited: detected and located | Edited: missed / wrong place | Clean: false alarm |
|---|---|---|---|
| Photo, original registered in TruthLens | 9 of 9 | 0 | 2 of 9 |
| Photo, not registered (blind) | 1 of 9 | 8 | 2 of 18 |
| Speech splice | 0 of 3 | 3 | 0 of 8 |
| Video inserted segment | 2 of 3 | 1 (trailer that already has cuts) | 0 of 6 |
| Caption date vs EXIF | 1 of 1 | 0 | 0 of 1 |

What this shows: comparison against a registered original is the reliable part. Blind detection of splices and copy-move
on real JPEGs, and of speech splices between similar studio recordings, is weak. We report that rather than hide it.

## Honest status of the numbers

No demo result is hardcoded: every score is computed live by the analysers. The thresholds and weights, however, were
**hand-tuned on synthetic samples generated for this demo** to contain the signals. There is no accuracy evaluation on real-world
data yet, so no accuracy figure is claimed.

## Evidence handling

The original uploads are stored read-only with their SHA-256 hash. Analysis works on derived files,
such as the extracted audio track. Every step is logged with a timestamp, and the hashes are re-verified
when the report is produced. The report can be exported as a PDF or JSON. Analysis history is kept per case.

## Architecture

```
browser (static HTML/JS)
   |  upload / demo
FastAPI  truthlens/app.py
   |
pipeline.py  INGEST -> PROVENANCE + MANIPULATION -> CORRELATE -> (AI review) -> SCORE -> REPORT
   ├─ registry.py          registered originals (SHA-256, pHash, metadata, C2PA status)
   ├─ forensics/provenance.py  registry match, diff, sensor-noise comparison, C2PA, hash history
   ├─ forensics/c2pa_check.py  C2PA validation via c2pa-python
   ├─ forensics/generator.py   EXPERIMENTAL frequency-domain generator indicators
   ├─ investigation.py     Investigation -> Finding table, "cannot establish"
   ├─ forensics/image.py   noise + ELA + EXIF
   ├─ forensics/video.py   cuts + per-frame noise tracking (reuses image.py)
   ├─ forensics/audio.py   window features, splice segments, spectrogram
   ├─ forensics/text.py    claim extraction
   ├─ forensics/correlate.py  cross-modal rules
   ├─ llm.py               optional Claude review (structured JSON, cites ids)
   └─ pdf.py               report export
data/cases/<case>/  evidence/ (read-only originals)  artifacts/  report.json
data/registry/      registry.json + files/
```

## Demo data

`samples/make_samples.py` generates a fictional "viral rally" scenario. The registered original is signed with C2PA using a throwaway demo
certificate, so it verifies as intact but its signer is not trusted. The set contains:
- an original photo and an edited copy (face swapped, re-saved in Photoshop)
- a video with a face region lacking sensor noise, an inserted segment at 4–5 s and a spliced, synthetic-sounding audio segment
- a caption claiming "unedited live footage recorded today", while the metadata says March 2019
- a synthetic-looking portrait (2x upsampled with checkerboard artefacts, no metadata)

All the people and events in it are fictional.

## Limitations

These are heuristic forensics, not trained deepfake classifiers. A fully generated image can be internally consistent,
and heavy platform recompression weakens the signals. Every finding lists the alternative explanations a human should rule out.

The measured QA results (108 cases, 19–20 Nov 2026), the full list of known limitations and the planned benchmark-first
model work are in [docs/QA.md](docs/QA.md). The demo script is in [docs/DEMO.md](docs/DEMO.md).
