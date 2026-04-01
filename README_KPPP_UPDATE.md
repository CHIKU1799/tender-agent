# Tender Agent — KPPP Karnataka Update

## What Changed in This Version

This update adds the **Karnataka KPPP scraper** and several infrastructure improvements to the dashboard, storage layer, and orchestrator. The project now covers **20 portals** and can retrieve live, archive, and awarded-contract data in a single run.

---

## New Portal: Karnataka KPPP (`kppp.karnataka.gov.in`)

### What It Is
The Karnataka Public Procurement Portal (KPPP) is the state government's e-procurement system covering all departments — roads, health, education, energy, rural development, and more.

### How It Works (Technical)
The KPPP frontend is an Angular app that talks to a Keycloak-authenticated REST API. Instead of scraping HTML, this agent calls the API directly:

```
POST https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/portal-service/search-eproc-tenders
Authorization: Bearer <JWT>
Cookie: AUTHROUTEID=.2
```

Each of the three procurement categories has its own endpoint:
- `GOODS` → `/portal-service/search-eproc-tenders`
- `WORKS` → `/portal-service/works/search-eproc-tenders`
- `SERVICES` → `/portal-service/services/search-eproc-tenders`

No Playwright/browser automation is needed for the listing endpoints — just plain HTTPS POST requests with a Bearer token.

### Scrape Matrix (9 Combinations)
The agent scrapes every combination of category × status:

| Category | Status | `scrape_type` label | What it contains |
|---|---|---|---|
| GOODS | PUBLISHED | `live` | Open goods tenders |
| GOODS | CLOSED | `archive` | Past-deadline goods tenders |
| GOODS | AWARDED | `awarded` | Awarded goods contracts |
| WORKS | PUBLISHED | `live` | Open civil/infrastructure works |
| WORKS | CLOSED | `archive` | Past-deadline works tenders |
| WORKS | AWARDED | `awarded` | Awarded works contracts |
| SERVICES | PUBLISHED | `live` | Open service tenders |
| SERVICES | CLOSED | `archive` | Past-deadline service tenders |
| SERVICES | AWARDED | `awarded` | Awarded service contracts |

Other status values (`CANCELLED`, `FREEZE`, `EVALUATION`, `APPROVED`) were probed and all return HTTP 400 — the API does not support them.

### Dataset Size
| Combo | Approx. records |
|---|---|
| GOODS / PUBLISHED | ~340 |
| GOODS / CLOSED | ~26 |
| GOODS / AWARDED | ~40 |
| WORKS / PUBLISHED | ~5,800 |
| WORKS / CLOSED | ~6,000 |
| WORKS / AWARDED | **45,000+** (multi-year archive) |
| SERVICES / PUBLISHED | ~1,500 |
| SERVICES / CLOSED | ~1,500 |
| SERVICES / AWARDED | ~1,500 |

> WORKS/AWARDED is Karnataka's entire historical road and infrastructure contract archive — 45,000+ records going back several years.

### Archive Cap (`max_archive_pages`)
To prevent hour-long scrapes, CLOSED and AWARDED fetches are capped at **100 pages (2,000 records) per combo** by default. The cap applies only to archive/awarded statuses — live (PUBLISHED) tenders are always fetched in full.

You can control this from the dashboard sidebar or API:

| Setting | Records | Time |
|---|---|---|
| 100 pages (default) | ~7,000–8,000 | ~5 min |
| 500 pages | ~30,000 | ~45 min |
| Blank (unlimited) | ~60,000+ | ~2–3 hrs |

---

## Infrastructure Changes

### 1. `scrape_type` field
Every tender record now carries a `scrape_type` field:
- `"live"` — currently open (PUBLISHED)
- `"archive"` — closed/past-deadline (CLOSED)
- `"awarded"` — contract awarded (AWARDED)

This field is stored in CSV, JSON, and SQLite and is used by the dashboard for filtering.

### 2. Dashboard: Scope radio now filters the table
Previously the "Active / Archive / Awarded" radio buttons only affected which data to *scrape*. Now they also **filter the displayed table** in real time — clicking "Awarded Contracts" immediately shows only awarded records without re-scraping.

### 3. Dashboard: New "Archive" stat card
The stats bar now shows three counts: **Total**, **Awarded**, **Archive** (and Portals). The Awarded count is driven by `scrape_type == "awarded"` rather than the `award_winner` field (which is empty in listing responses and requires detail-page fetches to populate).

### 4. Dashboard: Max Archive Pages control
A new **"Max Archive Pages"** input in the sidebar lets you control the archive depth independently of live tenders. Leave blank for unlimited.

### 5. Storage layer (`core/storage.py`)
Added `scrape_type` to `FULL_FIELDS` — the master column list shared by CSV, SQLite, and the DictWriter. Without this, `scrape_type` was silently dropped when saving and the dashboard always showed 0 awarded records.

### 6. Orchestrator (`core/orchestrator.py`)
Passes `max_archive_pages` from the dashboard filter payload down to agent.scrape() for agents that support it. Uses `hasattr` introspection so agents that don't have the parameter are unaffected.

---

## Files Changed

| File | Change |
|---|---|
| `agents/kppp_karnataka.py` | **New** — full KPPP scraper (608 lines) |
| `portals/configs.py` | Added KPPP portal config entry |
| `core/storage.py` | Added `scrape_type` to `FULL_FIELDS` |
| `core/orchestrator.py` | Added `max_archive_pages` plumbing |
| `interface/dashboard/app.py` | Added `scrape_type` filter, archive count in stats |
| `interface/dashboard/templates/index.html` | Scope radio → table filter; archive stat card; Max Archive Pages input |
| `core/browser.py` | Minor compatibility fix |

---

## Running the Dashboard

```bash
# Install dependencies
pip install -r requirements.txt

# Start the dashboard
python -m interface.dashboard.app
# Open http://127.0.0.1:5002
```

### Scraping KPPP from the dashboard
1. Select **Karnataka KPPP** in the portal list
2. Set **Max Archive Pages** (leave blank for full archive, or set 100 for a quick run)
3. Click **Start Scraping**
4. Use the **View** radio to switch between Live / Archive / Awarded / All Records

---

## Portal Coverage (20 Total)

| # | Portal | Type |
|---|---|---|
| 1 | Government e-Marketplace (GeM) | Central |
| 2 | Central Public Procurement Portal (CPPP) | Central |
| 3 | Ministry of Defence (defproc.gov.in) | Central |
| 4 | Indian Railways e-Procurement (IREPS) | Central |
| 5 | NIC eTenders (NHAI / MoRTH / BSNL) | Central |
| 6 | NTPC e-Procurement | PSU |
| 7 | BHEL e-Procurement | PSU |
| 8 | Coal India Tenders | PSU |
| 9 | ONGC e-Tender | PSU |
| 10 | HAL e-Procurement | PSU |
| 11 | **Karnataka KPPP** *(new)* | State |
| 12 | Karnataka e-Procurement | State |
| 13 | Maharashtra Tenders | State |
| 14 | Uttar Pradesh e-Tender | State |
| 15 | Tamil Nadu Tenders | State |
| 16 | Gujarat Tenders (nprocure) | State |
| 17 | Rajasthan e-Procurement | State |
| 18 | NHM / Health Ministry Tenders | Central |
| 19 | MeitY / Digital India Tenders | Central |
| 20 | Education Ministry Tenders (NEP) | Central |
