@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

where wsl >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo WSL was not found. Install WSL first.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%I in (`wslpath -u "%SCRIPT_DIR%"`) do set "WSL_DIR=%%I"

if not defined WSL_DIR (
    echo Could not resolve the project folder.
    pause
    exit /b 1
)

wsl bash -lc "cd '%WSL_DIR%' && bash ./run_in_wsl.sh"

if errorlevel 1 (
    echo.
    echo The script exited with an error.
    pause
)
