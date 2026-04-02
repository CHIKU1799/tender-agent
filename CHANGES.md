# Tender Agent — Cowork Enhancement Changelog

All changes made across Cowork sessions to enhance the multi-agent Indian government tender scraping system.

---

## New Portal Additions (9 User-Requested Sites)

- **tenderdetail.com** — Active, closed, and awarded tenders with pricing/awardee extraction
- **tendertiger.com** — Multi-URL fallback + tab navigation fallback for reliable scraping
- **govtprocurement.delhi.gov.in** — Delhi government procurement (GePNIC platform)
- **mptenders.gov.in** — Madhya Pradesh tenders (GePNIC platform)
- **uktenders.gov.in** — Uttarakhand tenders (GePNIC platform)
- **tenderkart.in** — Tender aggregator (generic platform)
- **tender247.com** — Multi-URL pattern fallback + card/table hybrid parsing
- **gujarattenders.in** — Gujarat tender aggregator (generic platform)
- **app.palladium.primenumbers.in** — SPA with API response interception for JSON data

## New Archive & Award Sources (10+ Sites)

Added credible archived/awarded tender sources including CPWD NIT, PGCIL, BSNL, EESL, IRCON, NHPC, SJVN, THDC India, and others — all with pricing and awardee data extraction.

## New Agent Files Created

| File | Purpose |
|------|---------|
| `agents/karnataka_eproc.py` | Full scraper for eproc.karnataka.gov.in using JSF/Seam POST pagination + Playwright fallback |
| `agents/tenderdetail.py` | Scraper for tenderdetail.com with award field enrichment |
| `agents/tendertiger.py` | Multi-URL fallback scraper with tab navigation fallback |
| `agents/tender247.py` | Multi-URL pattern scraper with card/table hybrid parsing |
| `agents/palladium.py` | SPA API interception agent mapping 20+ API fields |
| `agents/gujarat.py` | Gujarat nprocure.com agent with API interception + AOC scraping |
| `agents/karnataka_archive.py` | Karnataka archive agent with JSF status dropdown manipulation |
| `agents/gepnic_archive.py` | 3-strategy GePNIC archive agent (DirectLink API, Form search, Page scrape) |
| `agents/cppp_archive.py` | CPPP archive/awards agent |
| `agents/gem_archive.py` | GeM archive/awards agent |
| `agents/universal.py` | Universal fallback agent |

## New Portal Registry Files

| File | Count | Description |
|------|-------|-------------|
| `portals/extra_portals.py` | 40+ portals | State GePNIC portals with auto-URL generation from base_url |
| `portals/new_portals.py` | 25+ portals | All 9 user-requested + archive sources + Karnataka/Gujarat specific |

## Major Bug Fixes

### BaseAgent Constructor Signature
- Changed `BaseAgent.__init__` to accept optional `browser` parameter
- Fixed 8+ agent files that were passing `(cfg, session)` to `super().__init__()`

### PortalConfig Missing `results_url`
- Added `results_url` field and `__post_init__` auto-population in `extra_portals.py`
- GePNIC portals now auto-generate `session_seed_url`, `results_url`, `archive_url`, `awards_url` from `base_url`

### `self.cfg` vs `self.config` Mismatch
- Standardized all agent files to use `self.config` (matching BaseAgent)

### SPA/Card Layout Detection
- Rewrote `GenericAgent` with 3-strategy extraction: TABLE_JS → CARD_JS → LINKS_JS
- Now handles modern card/div/list layouts, not just HTML tables

### Disabled Portal Handling
- Added `enabled` field to PortalConfig
- Orchestrator returns no-op agent for disabled portals (DNS failures, dead sites)
- Disabled: BHEL, gspl_nprocure, gsecl_nprocure

## Enhanced Data Extraction

### Pricing & Awardee Fields
- Added award-specific fields across all agents: `tender_value_inr`, `award_winner`, `award_date`, `award_amount`, `contract_value`, `emd_amount`, `tender_fee`
- `core/storage.py` updated with `AWARD_FIELDS` list and `save_awards_csv()` function
- `GenericAgent` HEURISTIC_MAP expanded with 14 field patterns including award/pricing regex

### Karnataka eProc (New Dedicated Scraper)
- JSF/Seam POST pagination with `javax.faces.ViewState` and `dataScrollerIdidx{page}` params
- Stealth `requests.Session` with JSESSIONID management
- Playwright fallback with AI CAPTCHA solver
- Detail page enrichment for awardee/pricing data

## Orchestrator Overhaul (`core/orchestrator.py`)

- Complete `_make_agent()` rewrite with routing for ALL platform types in both active and archive scopes
- Platforms supported: gepnic, gem_api, ireps, cppp, generic, tenderdetail, tendertiger, tender247, palladium, karnataka_seam, karnataka_eproc
- Disabled portal check before agent instantiation
- Scope-aware routing: active, archive, awards, both, all

## Portal Count

**Total portals registered: ~85** across configs.py, extra_portals.py, and new_portals.py

## AI / API

- System uses **OpenAI GPT-4o** for CAPTCHA solving (image) and content extraction
- Whisper API for audio CAPTCHA transcription
- 2Captcha integration for reCAPTCHA challenges
