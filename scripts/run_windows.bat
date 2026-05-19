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
"%PYTHON%" -m streamlit run app.py

pause
