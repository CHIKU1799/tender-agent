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
playwright install chromium --with-deps --quiet 2>/dev/null || playwright install chromium

# 5. Create .env if missing
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "  ⚠   .env created from .env.example"
  echo "      Open .env and add your OpenAI API key:"
  echo "      OPENAI_API_KEY=sk-proj-..."
  echo ""
else
  echo "  ✓  .env already exists"
fi

# 6. Create runtime dirs
mkdir -p output logs screenshots

echo ""
echo "  ✓  Setup complete!"
echo ""
echo "  To start the dashboard:"
echo "    source .venv/bin/activate   # if not already active"
echo "    python3 dashboard.py"
echo "    → http://localhost:5002"
echo ""
echo "  To run all portals from the terminal:"
echo "    python3 run_all.py"
echo ""
