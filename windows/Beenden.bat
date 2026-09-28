@echo off
rem Beendet einen im Hintergrund laufenden Homify-Server.
cd /d "%~dp0.."
if not exist "data\homify.pid" goto :notrunning
set /p HOMIFY_PID=<"data\homify.pid"
rem Nur beenden, wenn die gespeicherte Prozess-ID wirklich zu Python gehoert
tasklist /FI "PID eq %HOMIFY_PID%" 2>nul | find /I "python" >nul
if errorlevel 1 goto :stale
taskkill /PID %HOMIFY_PID% /T /F >nul 2>nul
del "data\homify.pid" >nul 2>nul
echo Homify wurde beendet.
timeout /t 3 >nul
exit /b 0

:stale
del "data\homify.pid" >nul 2>nul
:notrunning
echo Homify scheint nicht zu laufen.
timeout /t 3 >nul
exit /b 0
