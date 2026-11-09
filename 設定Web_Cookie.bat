@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
echo ====================================================
echo Gemini Web Cookie Auto-Setup and Verification Tool
echo ====================================================

if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Creating Python virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [Error] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -c "import PIL, curl_cffi, pydantic" 2>nul
if errorlevel 1 (
    echo [Setup] Installing or updating required packages ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [Error] Failed to install dependencies.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m src.image_generator.cookie_fetcher

echo.
pause
