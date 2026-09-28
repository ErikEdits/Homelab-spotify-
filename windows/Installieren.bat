@echo off
setlocal EnableExtensions
title Homify - Installation
cd /d "%~dp0.."
set "PYTHONUTF8=1"

echo.
echo  ==================================================
echo    Homify - dein eigenes Spotify fuers Homelab
echo    Installation fuer Windows
echo  ==================================================
echo.

rem ---------------------------------------------------------------
rem  1. Python 3.10 oder neuer suchen
rem ---------------------------------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :havepython
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :havepython

echo  Python 3.10 oder neuer wurde nicht gefunden.
where winget >nul 2>nul
if errorlevel 1 goto :nopython
choice /C JN /M " Soll Python 3.12 jetzt automatisch installiert werden"
if errorlevel 2 goto :nopython
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo  Python wurde installiert. Bitte dieses Fenster schliessen und
echo  windows\Installieren.bat NOCHMAL starten.
echo.
pause
exit /b 0

:nopython
echo.
echo  Bitte Python von https://www.python.org/downloads/ installieren.
echo  WICHTIG: Beim Installieren "Add python.exe to PATH" anhaken.
echo  Danach dieses Skript erneut starten.
echo.
pause
exit /b 1

:havepython
echo  [1/5] Python gefunden:
%PY% --version

rem ---------------------------------------------------------------
rem  2. Eigene Programmumgebung (.venv) anlegen
rem ---------------------------------------------------------------
echo.
echo  [2/5] Erstelle Programmumgebung .venv ...
if exist ".venv\Scripts\python.exe" goto :venvok
%PY% -m venv .venv
if errorlevel 1 goto :fail
:venvok
set "VPY=.venv\Scripts\python.exe"

rem ---------------------------------------------------------------
rem  3. Pakete installieren
rem ---------------------------------------------------------------
echo.
echo  [3/5] Installiere Pakete ...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------
rem  4. spotDL fuer Downloads einrichten (eigene Umgebung)
rem ---------------------------------------------------------------
echo.
echo  [4/5] Richte spotDL ein (das dauert ein paar Minuten) ...
"%VPY%" -m homify setup
if errorlevel 1 echo  Hinweis: spotDL konnte nicht eingerichtet werden. Das geht spaeter auch in Homify unter Einstellungen.

rem ---------------------------------------------------------------
rem  5. Verknuepfungen + Windows-Firewall
rem ---------------------------------------------------------------
echo.
echo  [5/5] Verknuepfungen und Firewall-Freigabe ...
powershell -NoProfile -ExecutionPolicy Bypass -File "windows\setup-windows.ps1"

echo.
echo  ==================================================
echo    Fertig! Homify startet jetzt.
echo    Spaeter einfach die Verknuepfung "Homify" auf dem
echo    Desktop benutzen.
echo  ==================================================
echo.
start "" wscript.exe "%~dp0Homify.vbs"
timeout /t 8 >nul
exit /b 0

:fail
echo.
echo  FEHLER bei der Installation. Bitte die Meldungen oben pruefen.
echo.
pause
exit /b 1
