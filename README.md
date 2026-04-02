# 🏛️ Tender Agent

**AI-powered scraper for Indian government procurement portals.**
Scrapes active tenders, past/archive tenders, and awarded contracts from 19 portals — presented in a clean web dashboard.

---

## Features

- **19 portals** — CPPP, GeM, DefProc, eTenders, NTPC, Coal India, ONGC, IREPS, Maharashtra, UP, Tamil Nadu, Rajasthan, Karnataka, and more
- **Web dashboard** at `http://localhost:5002` — search, filter, sort, export
- **AI CAPTCHA bypass** — GPT-4o vision solves image CAPTCHAs on archive/awards pages
- **Archive + Awards** — past closed tenders and who-won contract data
- **Export** — CSV, JSON, SQLite — every run auto-saves to `output/`
- **Live progress** — real-time per-portal updates in the browser

---

## Quickstart

### Option A — Docker (recommended)

> No Python setup needed — just Docker.

```bash
# 1. Clone
git clone https://github.com/CHIKU1799/tender-agent.git
cd tender-agent

# 2. Set your OpenAI key
echo "OPENAI_API_KEY=sk-proj-your-key-here" > .env

# 3. Start
docker compose up --build
```

Open **http://localhost:5002**

---

### Option B — Local Python (3.9+)

```bash
# 1. Clone
git clone https://github.com/CHIKU1799/tender-agent.git
cd tender-agent

# 2. One-command setup (virtualenv + deps + Chromium)
bash setup.sh

# 3. Add your OpenAI key
nano .env    # set OPENAI_API_KEY=sk-proj-your-key-here

# 4. Start
source .venv/bin/activate
python3 dashboard.py
```

Open **http://localhost:5002**

---

## Using the Dashboard

1. **Select portals** in the left sidebar (grouped by category)
2. **Choose scope** — Active / Archive / Awards / Both
3. **Click ▶ Start Scraping** — live progress bar appears
4. **Search, filter, sort** the results table
5. **Export** via the CSV or JSON buttons

---

## Terminal Scraper (no dashboard)

```bash
# Scrape all 19 portals (2 pages each), save to output/
python3 run_all.py

# Interactive CLI with portal selection
python3 main.py
```

---

## Portal Coverage

| Portal | Category | Notes |
|--------|----------|-------|
| CPPP (eprocure.gov.in) | Central | No CAPTCHA, paginated |
| Government e-Marketplace (GeM) | Central | Live API |
| Ministry of Defence (DefProc) | Central | GePNIC + DirectLink bypass |
| NIC eTenders (NHAI / BSNL) | Central | GePNIC + DirectLink bypass |
| Indian Railways (IREPS) | Central | Struts2 form |
| NTPC | PSU | GePNIC + DirectLink bypass |
| Coal India | PSU | GePNIC + DirectLink bypass |
| ONGC | PSU | Generic scraper |
| Maharashtra | State | GePNIC + DirectLink bypass |
| Uttar Pradesh | State | GePNIC + DirectLink bypass |
| Tamil Nadu | State | GePNIC + DirectLink bypass |
| Rajasthan | State | GePNIC + DirectLink bypass |
| Karnataka | State | JSF/Seam form, no CAPTCHA |
| Gujarat | State | GePNIC + DirectLink bypass |
| BHEL, HAL | PSU | SAP Ariba (partial) |
| NHM / MeitY / Education | Info | Ministry HTML pages |

**Archive & Awards pages** use **GPT-4o vision** to bypass image CAPTCHAs automatically.

---

## Project Structure

```
tender-agent/
├── agents/
│   ├── gepnic.py           # GePNIC active tenders (14 portals)
│   ├── gepnic_archive.py   # Archive + awards with AI CAPTCHA
│   ├── gem.py              # GeM via route interception
│   ├── cppp.py             # CPPP paginated scraper
│   ├── karnataka.py        # Karnataka JSF/Seam portal
│   ├── ireps.py            # Indian Railways
│   └── generic.py          # Fallback scraper
├── ai/
│   ├── client.py           # OpenAI GPT-4o singleton
│   └── captcha_solver.py   # Screenshot → GPT-4o → solution
├── core/
│   ├── browser.py          # Playwright session manager
│   ├── storage.py          # CSV / JSON / SQLite export
│   └── orchestrator.py     # Parallel scraping + SSE events
├── interface/
│   ├── cli.py              # Terminal UI
│   └── dashboard/          # Flask web dashboard
├── portals/configs.py      # All 19 portal definitions
├── dashboard.py            # Web entry point
├── main.py                 # CLI entry point
├── run_all.py              # Batch all portals
├── Dockerfile
├── docker-compose.yml
└── setup.sh
```

---

## Configuration

Copy `.env.example` → `.env`:

```env
# Required ONLY for archive/awards CAPTCHA bypass
OPENAI_API_KEY=sk-proj-your-key-here

# Dashboard (optional)
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=5002
```

> Active tender scraping works **without** an OpenAI key.
> The key is only needed for archive/past tenders and award results (which have image CAPTCHAs).

---

## Output Files

Saved to `output/` after every run:

| File | Contents |
|------|----------|
| `{portal}_tenders.csv` | Per-portal data |
| `all_tenders.csv` | All portals combined (47 columns) |
| `awarded_tenders.csv` | Tenders with winner/AOC data |
| `tenders.db` | SQLite (all portals) |

---

## Requirements

| Requirement | Version |
|------------|---------|
| Python | 3.9+ |
| Chromium | Auto-installed by `setup.sh` |
| OpenAI key | For archive/awards only |

---

## Ethical Use

This tool scrapes **public government procurement data** legally required to be disclosed under India's Government e-Procurement Policy. Please:

- Respect the built-in delays between requests (1–4 seconds)
- Do not use for scraping at high volume or in violation of portal terms
- This is intended for research, business intelligence, and compliance monitoring
