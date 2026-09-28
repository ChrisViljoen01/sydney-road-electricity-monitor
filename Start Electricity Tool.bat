@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo The application environment has not been set up yet.
  echo Run "Setup Environment.bat" first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m electricity_tool
if errorlevel 1 pause
