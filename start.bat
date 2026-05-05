@echo off
cd /d %~dp0

setlocal EnableDelayedExpansion

REM Find available port from 5000 to 5010
set "PORT=5000"
:find_port
netstat -ano | findstr ":%PORT%" >nul 2>&1
if not errorlevel 1 (
    set /a PORT+=1
    if !PORT! LEQ 5010 goto :find_port
    echo [ERROR] Ports 5000-5010 all occupied
    pause
    exit /b 1
)

echo Starting on port %PORT%...

REM Auto-open browser
set "AUTO_OPEN=true"

REM Run with error capture
.venv\Scripts\python.exe app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start, check logs above
)
pause
