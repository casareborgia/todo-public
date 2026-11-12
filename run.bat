@echo off
cd /d "%~dp0"

rem 0) Bundled Python (runtime folder) - works on PCs without Python installed
if exist "%~dp0runtime\pythonw.exe" (
    "%~dp0runtime\python.exe" -c "import tkinter, sqlite3, ctypes" > nul 2>&1
    if errorlevel 1 (
        echo [ERROR] The bundled Python in the runtime folder is damaged or blocked.
        echo         Files may have been removed by security software during transfer.
        echo         Run check.bat to see which files are missing.
        pause
        exit /b 1
    )
    start "" "%~dp0runtime\pythonw.exe" "%~dp0main.py"
    exit /b 0
)

rem 1) Python 3.12 standard install paths (per-user / all-users / 32-bit)
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312-32\pythonw.exe"
    "%ProgramFiles%\Python312\pythonw.exe"
    "%ProgramFiles(x86)%\Python312-32\pythonw.exe"
) do (
    if exist %%P (
        start "" %%P "%~dp0main.py"
        exit /b 0
    )
)

rem 2) Python launcher (pyw)
where pyw > nul 2>&1
if not errorlevel 1 (
    pyw -3.12 -c "import tkinter" > nul 2>&1
    if not errorlevel 1 (
        start "" pyw -3.12 "%~dp0main.py"
        exit /b 0
    )
)

rem 3) python on PATH (skip the Microsoft Store alias stub, which fails this check)
python -c "import tkinter" > nul 2>&1
if not errorlevel 1 (
    python "%~dp0main.py"
    if errorlevel 1 pause
    exit /b 0
)

echo [ERROR] Python 3.12 (with tkinter) was not found.
echo         Use the source bundle with the runtime folder (scripts\build_source_bundle.py),
echo         install Python 3.12 from python.org, or use the portable exe build.
pause
exit /b 1
