@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating the isolated Python environment...
  python -m venv .venv
  if errorlevel 1 goto :error
)

echo Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo Installing the Electricity Dashboard requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

set "ARTIFACT_MODULES=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
if exist "%ARTIFACT_MODULES%" (
  if not exist "reporting_runtime\node_modules" (
    echo Connecting the spreadsheet reporting runtime...
    mklink /J "reporting_runtime\node_modules" "%ARTIFACT_MODULES%" >nul
    if errorlevel 1 goto :error
  )
) else (
  echo Warning: spreadsheet runtime was not found. PDF and CSV reports remain available.
)

echo.
echo Environment ready.
pause
exit /b 0

:error
echo.
echo Environment setup failed. Review the error above.
pause
exit /b 1
