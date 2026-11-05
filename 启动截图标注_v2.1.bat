@echo off
rem Marvis Screenshot Assistant v2.1 launcher - no console window (pythonw), auto install deps
cd /d "%~dp0"
set "PY=python"
where pythonw >nul 2>nul
if not errorlevel 1 set "PY=pythonw"
"%PY%" -c "import PIL, win32clipboard, keyboard" >nul 2>nul
if errorlevel 1 (
    echo Installing missing dependencies (pillow, pywin32, keyboard)...
    python -m pip install --disable-pip-version-check -i https://mirrors.cloud.tencent.com/pypi/simple pillow pywin32 keyboard
    if errorlevel 1 (
        python -m pip install --disable-pip-version-check pillow pywin32 keyboard
    )
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. Run manually: python -m pip install pillow pywin32 keyboard
        pause
        exit /b 1
    )
)
start "" "%PY%" "%~dp0screenshot_annotator.py"
exit /b 0
