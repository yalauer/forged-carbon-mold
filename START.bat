@echo off
setlocal
if not exist ".venv\Scripts\python.exe" (
  echo Python-Umgebung fehlt. Bitte zuerst die Installationsschritte aus README.md ausfuehren.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8765"
".venv\Scripts\python.exe" -m forged_carbon_mold
