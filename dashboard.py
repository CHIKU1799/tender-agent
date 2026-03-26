"""
Tender Agent Web Dashboard
Run: python dashboard.py
Opens at: http://localhost:5000
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv optional

# ── Validate OpenAI key ───────────────────────────────────────────────────────
openai_key = os.getenv("OPENAI_API_KEY", "")
if not openai_key:
    print("⚠  OPENAI_API_KEY not set. CAPTCHA solving will be unavailable.")
    print("   Add it to .env or export it:  export OPENAI_API_KEY=sk-proj-...")

# ── Ensure logs/output dirs ───────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
Path("output").mkdir(exist_ok=True)
Path("screenshots").mkdir(exist_ok=True)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/dashboard.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# ── Launch Flask ──────────────────────────────────────────────────────────────
from interface.dashboard.app import app

if __name__ == "__main__":
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    print(f"\n  🏛️  Tender Agent Dashboard")
    print(f"  ➜  http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, threaded=True)
