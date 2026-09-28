@echo off
rem Homify bei jeder Windows-Anmeldung unsichtbar im Hintergrund starten.
rem Zum Entfernen:  Autostart-einrichten.bat entfernen
cd /d "%~dp0.."
if /I "%~1"=="entfernen" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "windows\setup-windows.ps1" -RemoveAutostart
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "windows\setup-windows.ps1" -Autostart
)
pause
