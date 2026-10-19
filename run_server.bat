@echo off
cd /d %~dp0
if not exist ".venv\Scripts\activate.bat" (
    echo Run: python -m venv .venv  &&  .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python -m app.main
