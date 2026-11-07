@echo off
cd /d "%~dp0"
echo ====================================================
echo Starting Picture Style Studio (Dual Engine AI ImageGen)
echo Models: Gemini (Default) / Codex (ChatGPT)
echo ====================================================

python "src\server.py"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Server stopped with error.
    pause
)
