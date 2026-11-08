@echo off
cd /d "%~dp0"
echo ====================================================
echo Gemini Web Cookie Auto-Setup and Verification Tool
echo ====================================================

if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Creating Python virtual environment .venv ...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" -m src.image_generator.cookie_fetcher

echo.
pause
