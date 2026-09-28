@echo off
rem Startet Homify mit sichtbarem Konsolenfenster (praktisch zur Fehlersuche).
title Homify
cd /d "%~dp0.."
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Homify ist noch nicht installiert. Bitte zuerst windows\Installieren.bat ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m homify run --open
echo.
echo Homify wurde beendet.
pause
