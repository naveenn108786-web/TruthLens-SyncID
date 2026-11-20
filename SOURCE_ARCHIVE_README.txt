TruthLens + Sync ID source distribution

The complete application source, tests, documentation, bundled demo media, and offline face-detector model are included.

Quick start (Windows, from this extracted folder):
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  run-syncid.bat
  run.bat  (in another terminal)

Install FFmpeg and FFprobe and ensure both are available on PATH.
TruthLens: http://127.0.0.1:8000
Sync ID API: http://127.0.0.1:8001/docs

Tests:
  .venv\Scripts\python.exe -m pytest -q tests syncid/tests

Simulated producer demonstration (after starting both services):
  .venv\Scripts\python.exe -m syncid.demo --truthlens-url http://127.0.0.1:8000

Signing keys and registry initialize under your home directory's .syncid folder.
No private signing key or service credential is included in this archive.

Excluded: virtual environment, caches, runtime databases/evidence, generated outputs,
downloaded third-party evaluation images, and credentials.
Optional real-media camera tests skip if their evaluation samples are absent.
Use samples/eval_assessment.py --fetch to obtain the source-labelled evaluation files.

Sync ID is a prototype proposed protocol. AI-generation assessment can remain inconclusive.
See README.md, syncid/README.md, docs/QA.md and docs/BUGFIX_VALIDATION.md for scope and limitations.
