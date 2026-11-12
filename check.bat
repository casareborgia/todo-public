@echo off
rem Checks the source bundle: missing/changed files and whether the app can be loaded.
cd /d "%~dp0"
if not exist "%~dp0runtime\python.exe" (
    echo [ERROR] runtime\python.exe not found. The runtime folder is missing or was removed.
    pause
    exit /b 1
)
"%~dp0runtime\python.exe" "%~dp0scripts\verify_bundle.py"
pause
