@echo off
rem Double-click to start Hand&Voice. If it fails, the window stays open so you can read the error.
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo The virtual environment "venv" was not found in this folder.
    echo Run these two commands once in PowerShell, then double-click this file again:
    echo   py -3.11 -m venv venv
    echo   .\venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
"venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo Hand^&Voice stopped with an error. Please copy or screenshot the text above.
    pause
)
