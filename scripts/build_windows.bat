@echo off
setlocal

cd /d "%~dp0\.."

where py >nul 2>nul
if errorlevel 1 (
  echo Python is required. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv" (
  py -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
)

set "PYTHON=.venv\Scripts\python.exe"
"%PYTHON%" -m pip install --upgrade pip
"%PYTHON%" -m pip install -r requirements.txt
"%PYTHON%" scripts\generate_icons.py
set PYINSTALLER_CONFIG_DIR=%CD%\build\pyinstaller-cache
if exist "dist\RunningHeatmap" rmdir /s /q "dist\RunningHeatmap"
if exist "build\RunningHeatmap" rmdir /s /q "build\RunningHeatmap"
"%PYTHON%" -m PyInstaller --clean --noconfirm RunningHeatmap.spec
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path 'dist\RunningHeatmap-windows.zip') { Remove-Item 'dist\RunningHeatmap-windows.zip' }; Compress-Archive -Path 'dist\RunningHeatmap' -DestinationPath 'dist\RunningHeatmap-windows.zip'"
"%PYTHON%" scripts\check_release.py dist\RunningHeatmap-windows.zip

echo.
echo Build complete:
echo   dist\RunningHeatmap\RunningHeatmap.exe
echo   dist\RunningHeatmap-windows.zip

pause
