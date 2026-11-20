@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
echo Sync ID API: http://127.0.0.1:8001/docs
echo Local demo only. Signing key and registry default to %%USERPROFILE%%\.syncid\
%PY% -m uvicorn syncid.app:app --host 127.0.0.1 --port 8001
