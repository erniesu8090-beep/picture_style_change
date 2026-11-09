@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
echo ====================================================
echo Starting Picture Style Studio (Triple Engine AI Studio)
echo Models: Gemini (Default) / Codex (ChatGPT) / Gemini Web
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

".venv\Scripts\python.exe" "src\server.py"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Server stopped with error.
    pause
)
