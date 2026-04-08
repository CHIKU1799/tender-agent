#!/usr/bin/env bash
# ── Tender Agent — Run launcher ───────────────────────────────────────────────
# Usage:
#   ./run.sh                 # launch dashboard (default)
#   ./run.sh dashboard       # launch dashboard at http://localhost:5002
#   ./run.sh all             # run all portals once (run_all.py)
#   ./run.sh cli             # interactive CLI (main.py)
#   ./run.sh cppp            # scrape CPPP full
#   ./run.sh gujarat         # scrape Gujarat portals
#   ./run.sh karnataka       # scrape Karnataka portals
#   ./run.sh new             # scrape new portals (BSNL/NHPC/AP/TS/WB/Bihar/CG)
#   ./run.sh refresh         # full refresh across all portals
set -e

cd "$(dirname "$0")"

# Activate venv if present and not already active
if [ -z "$VIRTUAL_ENV" ] && [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

CMD="${1:-dashboard}"

case "$CMD" in
  dashboard|dash|ui)
    echo "→ Launching dashboard at http://localhost:5002"
    python3 dashboard.py
    ;;
  all)
    echo "→ Running all portals (run_all.py)"
    python3 run_all.py
    ;;
  cli|main|interactive)
    echo "→ Launching interactive CLI (main.py)"
    python3 main.py
    ;;
  cppp)
    echo "→ Scraping CPPP (full)"
    python3 scrape_cppp_full.py
    ;;
  gujarat|guj)
    echo "→ Scraping Gujarat portals"
    python3 scrape_gujarat.py
    ;;
  karnataka|kar|kppp)
    echo "→ Scraping Karnataka portals"
    python3 scrape_karnataka.py
    ;;
  new|new-portals)
    echo "→ Scraping new portals (BSNL/NHPC/AP/TS/WB/Bihar/CG)"
    python3 scrape_new_portals.py
    ;;
  refresh|all-refresh|full)
    echo "→ Full refresh across all portals"
    python3 scrape_all.py
    ;;
  -h|--help|help)
    sed -n '2,13p' "$0"
    ;;
  *)
    echo "✗ Unknown command: $CMD"
    echo "Run './run.sh help' for usage."
    exit 1
    ;;
esac
