@echo off
rem Holt die neueste Homify-Version (falls per git geklont) und aktualisiert Pakete + spotDL.
title Homify - Aktualisieren
cd /d "%~dp0.."
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Homify ist noch nicht installiert. Bitte zuerst windows\Installieren.bat ausfuehren.
  pause
  exit /b 1
)
call "windows\Beenden.bat" >nul 2>nul
where git >nul 2>nul
if not errorlevel 1 if exist ".git" git pull
".venv\Scripts\python.exe" -m pip install -r requirements.txt --upgrade --disable-pip-version-check
".venv\Scripts\python.exe" -m homify setup
echo.
echo Fertig. Homify startet neu ...
start "" wscript.exe "%~dp0Homify.vbs"
timeout /t 5 >nul
