#!/usr/bin/env bash
# ── Tender Agent — One-command local setup ───────────────────────────────────
set -e

echo ""
echo "  🏛️  Tender Agent — Setup"
echo "  ─────────────────────────────────────────────────────────────────"

# 1. Python version check
PY=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
MAJOR=$(echo $PY | cut -d. -f1)
MINOR=$(echo $PY | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
  echo "  ✗  Python 3.9+ required (found $PY)"
  exit 1
fi
echo "  ✓  Python $PY"

# 2. Create virtualenv if not active
if [ -z "$VIRTUAL_ENV" ]; then
  if [ ! -d ".venv" ]; then
    echo "  →  Creating virtualenv at .venv ..."
    python3 -m venv .venv
  fi
  source .venv/bin/activate
  echo "  ✓  Virtualenv activated"
fi

# 3. Install Python deps
echo "  →  Installing Python dependencies ..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 4. Install Playwright browsers
echo "  →  Installing Playwright Chromium ..."
playwright install chromium --with-deps --quiet 2>/dev/null || playwright install chromium --with-deps

# 5. Create .env if missing
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "  ⚠   .env created from .env.example"
  echo "      Open .env and set your keys:"
  echo "        OPENAI_API_KEY   — for image & audio CAPTCHA (required for archive/awards)"
  echo "        TWOCAPTCHA_KEY   — for reCAPTCHA v2/v3 & hCaptcha (optional)"
  echo "        ANTICAPTCHA_KEY  — alternative to 2captcha (optional)"
  echo ""
else
  echo "  ✓  .env already exists"
fi

# 6. Create runtime dirs
mkdir -p output logs screenshots

echo ""
echo "  ✓  Setup complete!"
echo ""
echo "  ─── CAPTCHA capabilities ───────────────────────────────────────────"
echo "  Image/Text:  GPT-4o (OPENAI_API_KEY)"
echo "  Math/Slider: Built-in (no API needed)"
echo "  reCAPTCHA:   2captcha.com (TWOCAPTCHA_KEY) or anti-captcha.com"
echo "  hCaptcha:    2captcha.com (TWOCAPTCHA_KEY)"
echo "  Audio:       OpenAI Whisper (OPENAI_API_KEY)"
echo ""
echo "  ─── Running ────────────────────────────────────────────────────────"
echo "  Dashboard:   source .venv/bin/activate && python3 dashboard.py"
echo "               → http://localhost:5002"
echo ""
echo "  All portals: python3 run_all.py"
echo "  Test scrape: python3 test_scrape.py"
echo ""
