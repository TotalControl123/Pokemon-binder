@echo off
REM Double-click this to open the binder window.
REM
REM It finds Python for you and starts binder_gui.py. Nothing here touches
REM PowerShell, so the execution-policy warnings don't apply.

cd /d "%~dp0"

REM 'py' is the Windows launcher and is the most reliable; fall back to python.
where py >nul 2>&1
if %errorlevel%==0 (
    start "" pyw binder_gui.py
    if errorlevel 1 py binder_gui.py
    goto :eof
)

where python >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw binder_gui.py
    if errorlevel 1 python binder_gui.py
    goto :eof
)

echo.
echo Python isn't on your PATH, so this can't start.
echo.
echo Install it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" on the first screen of the installer.
echo.
pause
