#!/usr/bin/env bash
# Start TruthLens on http://localhost:8000
# Optional: export ANTHROPIC_API_KEY=... to enable the AI claims check and summary.
set -e
cd "$(dirname "$0")"
[ -d samples/demo ] || python samples/make_samples.py
exec python -m uvicorn truthlens.app:app --host 0.0.0.0 --port "${PORT:-8000}"
