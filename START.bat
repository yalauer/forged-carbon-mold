@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python-Umgebung fehlt. Bitte zuerst die Installationsschritte aus README.md ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m forged_carbon_mold
