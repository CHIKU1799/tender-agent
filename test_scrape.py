"""
Quick test: scrape 1 portal from each category (max 2 pages each).
Categories: Central, PSU, State, Info
"""
from __future__ import annotations
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("logs").mkdir(exist_ok=True)
Path("output").mkdir(exist_ok=True)
Path("screenshots").mkdir(exist_ok=True)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/test_scrape.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

from core.browser import BrowserSession
from core.storage import save_csv, save_json, save_combined_csv, SnapshotStore, FULL_FIELDS
from portals.configs import PORTALS

# One portal per category
TEST_PORTALS = {
    "Central": "cppp",         # No CAPTCHA, paginated — most reliable
    "PSU":     "gem",          # GeM API via Playwright route interception
    "State":   "maharashtra",  # GePNIC DirectLink bypass (karnataka URL is 404)
    "PSU-2":   "ntpc",         # Second PSU: NTPC via GePNIC (Info portals block scrapers)
}


def make_agent(portal_id: str, session: BrowserSession):
    from agents.gepnic  import GePNICAgent
    from agents.gem     import GeMAgent
    from agents.cppp    import CPPPAgent
    from agents.generic import GenericAgent
    cfg = PORTALS[portal_id]
    if   cfg.platform == "gepnic":  return GePNICAgent(cfg, session)
    elif cfg.platform == "gem_api": return GeMAgent(cfg, session)
    elif cfg.platform == "cppp":    return CPPPAgent(cfg, session)
    else:                           return GenericAgent(cfg, session)


def print_table(tenders: list[dict], portal_id: str, max_rows: int = 10):
    """Print a pretty ASCII table of the tenders."""
    if not tenders:
        print(f"  (no tenders)")
        return

    rows_to_show = tenders[:max_rows]
    # Column widths
    W_TITLE = 50
    W_ORG   = 30
    W_DATE  = 12
    W_VAL   = 12

    sep  = f"+{'-'*(W_TITLE+2)}+{'-'*(W_ORG+2)}+{'-'*(W_DATE+2)}+{'-'*(W_DATE+2)}+{'-'*(W_VAL+2)}+"
    fmt  = f"| {{:<{W_TITLE}}} | {{:<{W_ORG}}} | {{:<{W_DATE}}} | {{:<{W_DATE}}} | {{:<{W_VAL}}} |"

    print(sep)
    print(fmt.format("Title", "Organisation", "Published", "Closing", "Value (₹)"))
    print(sep)

    for t in rows_to_show:
        title = (t.get("title") or "")[:W_TITLE]
        org   = (t.get("organisation") or "")[:W_ORG]
        pub   = (t.get("published_date") or "")[:W_DATE]
        cls   = (t.get("closing_date") or "")[:W_DATE]
        val   = (t.get("tender_value_inr") or "—")[:W_VAL]
        print(fmt.format(title, org, pub, cls, val))

    print(sep)
    if len(tenders) > max_rows:
        print(f"  ... and {len(tenders) - max_rows} more rows")


async def main():
    all_tenders = []
    summary = []

    print("\n" + "="*70)
    print("  TENDER AGENT — CATEGORY TEST SCRAPE")
    print("  Central: CPPP  |  PSU: GeM  |  State: Maharashtra  |  PSU-2: NTPC")
    print("  Note: Info portals (MeitY/NHM/Education) block automated access.")
    print("="*70 + "\n")

    async with BrowserSession(headless=True) as session:
        for category, portal_id in TEST_PORTALS.items():
            cfg = PORTALS[portal_id]
            print(f"\n{'─'*70}")
            print(f"  {cfg.emoji}  [{category}] {cfg.display_name}")
            print(f"{'─'*70}")

            agent = make_agent(portal_id, session)
            try:
                result = await agent.scrape(max_pages=2, progress_cb=None)

                if result.errors:
                    print(f"  ⚠  Errors: {result.errors}")
                if result.skipped:
                    print(f"  ⏭  Skipped: {result.skip_reason}")

                tenders = result.tenders
                print(f"  ✓  Scraped {len(tenders)} tenders across {result.pages} page(s)")
                print()
                print_table(tenders, portal_id)

                # Save per-portal
                if tenders:
                    save_csv(tenders, portal_id)
                    save_json(tenders, portal_id)
                    all_tenders.extend(tenders)

                summary.append({
                    "category":  category,
                    "portal":    portal_id,
                    "name":      cfg.display_name,
                    "tenders":   len(tenders),
                    "pages":     result.pages,
                    "errors":    len(result.errors),
                })

            except Exception as e:
                print(f"  ✗  CRASH: {e}")
                summary.append({
                    "category":  category,
                    "portal":    portal_id,
                    "name":      cfg.display_name,
                    "tenders":   0,
                    "pages":     0,
                    "errors":    1,
                })

    # Save combined CSV
    if all_tenders:
        combined = save_combined_csv(all_tenders)
        print(f"\n\n{'='*70}")
        print(f"  FILES SAVED")
        print(f"{'='*70}")
        for pid in TEST_PORTALS.values():
            csv_p = Path(f"output/{pid}_tenders.csv")
            if csv_p.exists():
                print(f"  • output/{pid}_tenders.csv")
        print(f"  • output/all_tenders.csv  ({len(all_tenders)} total rows)")

    # Final summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Category':<10} {'Portal':<12} {'Tenders':>8}  {'Pages':>6}  {'Errors':>7}")
    print(f"  {'-'*50}")
    total = 0
    for s in summary:
        icon  = "✓" if s["errors"] == 0 and s["tenders"] > 0 else ("⚠" if s["tenders"] > 0 else "✗")
        print(f"  {icon} {s['category']:<10} {s['portal']:<12} {s['tenders']:>8}  {s['pages']:>6}  {s['errors']:>7}")
        total += s["tenders"]
    print(f"  {'-'*50}")
    print(f"  {'TOTAL':<23} {total:>8}")
    print(f"\n  Dashboard: python3 dashboard.py → http://localhost:5000\n")


if __name__ == "__main__":
    asyncio.run(main())
