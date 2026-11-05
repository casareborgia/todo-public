@echo off
rem Portable folder build (dist\TodoCalendar_Portable)
rem - Run on an internet-connected PC; copy only the dist folder into the internal network.
rem - Output bitness follows the Python used. For a 32-bit (x86) build, point BUILD_PYTHON
rem   at a 32-bit Python, e.g.:
rem     set BUILD_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312-32\python.exe
cd /d "%~dp0\.."

set "PY=python"
if defined BUILD_PYTHON set "PY=%BUILD_PYTHON%"

"%PY%" -c "import tkinter" > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python with tkinter not found: %PY%
    pause
    exit /b 1
)
for /f %%B in ('call "%PY%" -c "import struct;print(struct.calcsize(chr(80))*8)"') do set "BITS=%%B"
echo Python: %PY% (%BITS%-bit build)

echo [1/3] Checking PyInstaller...
"%PY%" -m PyInstaller --version > nul 2>&1
if errorlevel 1 (
    "%PY%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed. This step needs internet access.
        echo         Build on an internet-connected PC and copy dist\TodoCalendar_Portable.
        pause
        exit /b 1
    )
)

echo [2/3] Building Portable Folder (dist\TodoCalendar_Portable)...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --windowed --name "TodoCalendar_Portable" main.py
if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo [3/3] Copying config, user guide and creating empty scan_inbox...
rem The app reads config.json / todo_data.db / scan_inbox next to the exe.
rem Sample files and the local todo_data.db are intentionally NOT shipped.
rem Watch folders added on this PC are reset to the default scan_inbox.
"%PY%" "scripts\make_dist_config.py" "config.json" "dist\TodoCalendar_Portable"
if errorlevel 1 (
    echo [ERROR] Finishing the dist folder failed.
    pause
    exit /b 1
)
echo Build Complete: dist\TodoCalendar_Portable\TodoCalendar_Portable.exe (%BITS%-bit)
pause
