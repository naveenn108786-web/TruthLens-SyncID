@echo off
REM Start TruthLens on Windows: http://localhost:8000
REM Optional: set ANTHROPIC_API_KEY=... before running to enable the AI claims check.
cd /d "%~dp0"
where ffmpeg >NUL 2>NUL || (echo ffmpeg was not found on PATH. Install it with:  winget install --id Gyan.FFmpeg  then open a NEW terminal. & exit /b 1)
where ffprobe >NUL 2>NUL || (echo ffprobe was not found on PATH. It comes with ffmpeg: winget install --id Gyan.FFmpeg & exit /b 1)
if exist .venv\Scripts\python.exe (set PY=.venv\Scripts\python.exe) else (set PY=python)
if not exist samples\demo\rally_original.jpg %PY% samples\make_samples.py
if "%PORT%"=="" set PORT=8000
echo Open http://localhost:%PORT% in your browser. Press Ctrl+C to stop.
%PY% -m uvicorn truthlens.app:app --host 127.0.0.1 --port %PORT%
