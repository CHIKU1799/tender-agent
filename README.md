# Tender Agent 🏛️

A multi-portal Indian Government tender scraping agent with an interactive terminal interface.

Supports **22 portals** — Central Govt, PSUs, State portals, and Ministry info pages.

---

## Supported Portals

### 🏛️ Central Government
| ID | Portal | Platform | Notes |
|----|--------|----------|-------|
| `gem` | Government e-Marketplace | REST API | No browser needed |
| `cppp` | CPPP / eprocure.gov.in | GePNIC | Largest central portal |
| `defproc` | Ministry of Defence | GePNIC | MES, BRO, Navy, AF |
| `ireps` | Indian Railways (IREPS) | Struts 2 | Zone-wise tenders |
| `etenders` | NHAI / MoRTH / BSNL | GePNIC | etenders.gov.in |

### 🏭 Public Sector Undertakings
| ID | Portal | Platform |
|----|--------|----------|
| `ntpc` | NTPC e-Procurement | GePNIC |
| `bhel` | BHEL e-Procurement | SAP Ariba |
| `coalindia` | Coal India Tenders | GePNIC |
| `ongc` | ONGC e-Tender | Custom |
| `hal` | HAL e-Procurement | Custom |

### 🗺️ State Portals
| ID | State | Platform |
|----|-------|----------|
| `karnataka` | Karnataka | GePNIC |
| `maharashtra` | Maharashtra | GePNIC |
| `up` | Uttar Pradesh | GePNIC |
| `tamilnadu` | Tamil Nadu | GePNIC |
| `gujarat` | Gujarat | GePNIC |
| `rajasthan` | Rajasthan | GePNIC |

### ℹ️ Ministry Info Portals
| ID | Portal |
|----|--------|
| `nhm` | NHM / Health Ministry |
| `meity` | MeitY / Digital India |
| `education` | Education Ministry (NEP) |

---

## Quick Start

```bash
# Clone
git clone https://github.com/CHIKU1799/tender-agent.git
cd tender-agent

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run the interactive agent
python main.py
```

---

## How It Works

### The Interactive Interface

Running `python main.py` opens a step-by-step terminal UI:

```
Step 1 — Select Portals
  Choose from presets (All Central Govt, All PSUs, Everything...)
  or pick individual portals with a checkbox menu

Step 2 — Configure Filters
  Max pages per portal (or all)
  Organisation name filter
  Whether to fetch detail pages (richer data, slower)

Step 3 — Export Formats
  CSV  — opens in Excel/Sheets
  JSON — full structured data
  SQLite — queryable database with all portals in one file

Step 4 — Live Progress
  Real-time progress bars per portal
  Running tender count

Step 5 — Summary
  Per-portal table: total, NEW since last run, pages scraped
  List of newly discovered tenders
```

---

## CAPTCHA Bypass (GePNIC Portals)

**14 out of 22 portals** run on NIC's GePNIC platform (defproc, cppp, etenders, ntpc, coalindia, karnataka, maharashtra, up, tamilnadu, gujarat, rajasthan, etc.).

All of them have a CAPTCHA on their search form. Here's how we bypass it entirely:

### Why it works
The NIC GePNIC platform uses Apache Tapestry's `DirectLink` component. The search results page is a stateful server-side component that renders the current session's result set.

When you navigate directly to:
```
{base_url}?component=$DirectLink&page=FrontEndAdvancedSearchResult&service=direct
```
...the server renders all active tenders as the default (no-filter) result set, **without validating that a search form was submitted**.

### The 3-step bypass
```
1. Visit any page on the portal → server creates a JSESSIONID session cookie
2. Navigate directly to the results URL above → server returns all active tenders
3. Paginate using the #linkFwd (Next >) link — no CAPTCHA at any step
```

### Why this is not cracking/circumventing security
- No CAPTCHA image is decoded or attacked
- No hidden form fields are forged
- The results URL is a publicly accessible endpoint
- The CAPTCHA only guards the **search form** — not the **results component**
- This is equivalent to bookmarking the results page directly

---

## Architecture

```
tender-agent/
├── main.py                  # Entry point — orchestrates the full run
│
├── agents/
│   ├── base.py              # BaseAgent abstract class
│   ├── gepnic.py            # GePNIC agent — covers 14 portals
│   ├── gem.py               # GeM REST API agent
│   ├── ireps.py             # Indian Railways agent
│   └── generic.py           # Fallback for unexplored platforms
│
├── portals/
│   └── configs.py           # All 22 portal configs (PortalConfig dataclasses)
│
├── core/
│   ├── browser.py           # Playwright session manager (shared across agents)
│   ├── storage.py           # CSV / JSON / SQLite exporters + snapshot/diff store
│   └── utils.py             # retry_async, parse_title_cell, helpers
│
├── interface/
│   └── cli.py               # Rich + Questionary terminal UI
│
└── requirements.txt
```

### Adding a New Portal

**For a GePNIC portal** — add one block to `portals/configs.py`:
```python
"myportal": PortalConfig(
    portal_id        = "myportal",
    display_name     = "My State Portal",
    base_url         = "https://mystate.gov.in/nicgep/app",
    platform         = "gepnic",
    category         = "State",
    session_seed_url = "https://mystate.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
    results_url      = "https://mystate.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
    emoji            = "🗺️",
),
```
Zero Python changes needed. The `GePNICAgent` handles it automatically.

**For a custom platform** — create a new agent class inheriting `BaseAgent`, register it in `main.py`'s `make_agent()` factory.

---

## Libraries Used

| Library | Purpose |
|---------|---------|
| `playwright` | Browser automation — loads pages, executes JS, clicks buttons |
| `playwright-stealth` | Hides `navigator.webdriver` and other bot fingerprints |
| `fake-useragent` | Rotates real Chrome/Firefox User-Agent strings between pages |
| `aiohttp` | HTTP client for GeM REST API (no browser needed for GeM) |
| `rich` | Terminal UI — progress bars, tables, panels, live updates |
| `questionary` | Interactive prompts — select, checkbox, confirm, text input |
| `pydantic` | Data validation for portal configs |
| `pyyaml` | Config file parsing |
| `aiosqlite` | Async SQLite writes for the unified tenders database |
| `pandas` | Post-processing and CSV export utilities |
| `asyncio` | Async I/O coordination across multiple portal agents |

---

## Output Files

After a run, all output is in `output/`:

```
output/
├── defproc_tenders.csv       # Per-portal CSV (append mode across runs)
├── defproc_tenders.json      # Per-portal JSON (overwritten each run)
├── cppp_tenders.csv
├── gem_tenders.json
├── ...
├── tenders.db                # Unified SQLite database (all portals, upsert)
└── snapshots/
    ├── defproc.json          # Previous run snapshot (for diff)
    ├── cppp.json
    └── ...
```

The SQLite database has a compound primary key on `(portal_id, tender_id)` — safe to run daily without duplicates.

---

## Daily Scheduling

```bash
# Add to crontab — runs every day at 7am
crontab -e
0 7 * * * cd ~/tender-agent && python3 main.py >> logs/cron.log 2>&1
```

For headless/non-interactive daily runs, pass arguments directly (coming in v2).

---

## Platform Support Status

| Platform | Status | Portals |
|----------|--------|---------|
| GePNIC | ✅ Full support + CAPTCHA bypass | 14 portals |
| GeM API | ✅ Full support | 1 portal |
| IREPS | 🔶 Basic support | 1 portal |
| SAP Ariba (BHEL) | ⚠️ Needs exploration | 1 portal |
| ONGC custom | ⚠️ Needs exploration | 1 portal |
| HAL custom | ⚠️ Needs exploration | 1 portal |
| Ministry info pages | 🔶 Generic extractor | 3 portals |

---

## Ethical Notes

- All scraped data is **publicly available** without login
- **2.5–5 second delays** between every page request
- No CAPTCHA cracking — only legitimate URL access patterns used
- No authentication bypassed — only public endpoints accessed
