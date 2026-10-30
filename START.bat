@echo off
setlocal
cd /d "%~dp0"
set "APP_URL=http://127.0.0.1:8765"
if not exist ".venv\Scripts\python.exe" (
  echo Python-Umgebung fehlt. Bitte zuerst die Installationsschritte aus README.md ausfuehren.
  pause
  exit /b 1
)

curl.exe --fail --silent --max-time 1 "%APP_URL%/api/status" >nul 2>nul
if errorlevel 1 (
  start "Forged Carbon Mold Server" cmd /k ""%CD%\.venv\Scripts\python.exe" -m forged_carbon_mold"
  for /l %%i in (1,1,30) do (
    curl.exe --fail --silent --max-time 1 "%APP_URL%/api/status" >nul 2>nul
    if not errorlevel 1 goto app_ready
    powershell.exe -NoProfile -NonInteractive -Command "Start-Sleep -Milliseconds 500"
  )
  echo Der CAD-Server antwortet nicht. Pruefe die Python-Ausgabe im Serverfenster und Port 8765.
  pause
  exit /b 1
)

:app_ready
start "" "%APP_URL%"
endlocal
