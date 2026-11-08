@echo off
cd /d "%~dp0"
echo ====================================================
echo Starting Picture Style Studio (Triple Engine AI Studio)
echo Models: Gemini (Default) / Codex (ChatGPT) / Gemini Web
echo ====================================================

if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Creating Python virtual environment .venv ...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" "src\server.py"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Server stopped with error.
    pause
)
