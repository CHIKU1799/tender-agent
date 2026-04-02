@echo off
:: ── Tender Agent — Windows Setup & Run ──────────────────────────────────────
setlocal enabledelayedexpansion

echo.
echo   🏛️  Tender Agent — Windows Setup
echo   ─────────────────────────────────────────────────────────────────

:: ── 1. Python version check ───────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo   ✗  Python not found. Install from https://python.org ^(3.9+^)
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo   ✓  Python !PY_VER!

:: ── 2. Create virtualenv ──────────────────────────────────────────────────
if not defined VIRTUAL_ENV (
    if not exist ".venv\" (
        echo   →  Creating virtualenv at .venv ...
        python -m venv .venv
    )
    call .venv\Scripts\activate.bat
    echo   ✓  Virtualenv activated
) else (
    echo   ✓  Virtualenv already active
)

:: ── 3. Install all dependencies ───────────────────────────────────────────
echo   →  Installing dependencies ...
python -m pip install --quiet --upgrade pip
pip install --quiet playwright playwright-stealth fake-useragent aiohttp pydantic pandas aiosqlite rich questionary pyyaml python-dotenv openai flask flask-cors python-dateutil DrissionPage
if errorlevel 1 (
    echo   ⚠  Some packages failed, trying requirements.txt ...
    pip install -r requirements.txt
)
echo   ✓  Dependencies installed

:: ── 4. Install Playwright Chromium ───────────────────────────────────────
echo   →  Installing Playwright Chromium ...
playwright install chromium --with-deps 2>nul
if errorlevel 1 (
    python -m playwright install chromium
)
echo   ✓  Playwright ready

:: ── 5. Create .env if missing ─────────────────────────────────────────────
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo.
        echo   ⚠   .env created — open it and add OPENAI_API_KEY
        echo.
    )
) else (
    echo   ✓  .env exists
)

:: ── 6. Create runtime directories ─────────────────────────────────────────
if not exist "output\"         mkdir output
if not exist "output\cookies\" mkdir output\cookies
if not exist "logs\"           mkdir logs
if not exist "screenshots\"    mkdir screenshots
echo   ✓  Directories ready

:: ── 7. Launch dashboard ───────────────────────────────────────────────────
echo.
echo   ─────────────────────────────────────────────────────────────────
echo   Opening dashboard at http://localhost:5002
echo   Press CTRL+C to stop
echo   ─────────────────────────────────────────────────────────────────
echo.

python dashboard.py

pause
