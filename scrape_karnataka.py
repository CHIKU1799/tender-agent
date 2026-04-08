#!/usr/bin/env python3
"""
Karnataka KPPP Standalone Scraper
──────────────────────────────────────────────────────────────────────
Scrapes https://kppp.karnataka.gov.in/#/portal/searchTender/live

Usage:
    python scrape_karnataka.py                   # scrape all tabs, all pages
    python scrape_karnataka.py --max-pages 5     # limit to 5 pages per tab
    python scrape_karnataka.py --tabs works      # only scrape "works" tab
    python scrape_karnataka.py --no-details      # skip clicking into each tender
    python scrape_karnataka.py --headed          # show browser (useful for debugging)

Data is saved incrementally after EVERY page to:
    output/karnataka_kppp_tenders.csv
    output/karnataka_kppp_tenders.json
    output/tenders.db  (SQLite)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# ── Setup paths & logging ────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

Path("logs").mkdir(exist_ok=True)
Path("screenshots").mkdir(exist_ok=True)
Path("output").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/karnataka_kppp.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("karnataka_scraper")

from dotenv import load_dotenv
load_dotenv()

from portals.configs import PORTALS
from agents.kppp import KPPPAgent
from core.browser import BrowserSession
from core.storage import save_csv, save_json, save_sqlite, save_combined_csv, save_daily_snapshot, OUTPUT_DIR
from ai.classifier import classify_tender_local


# ── Incremental save callback ────────────────────────────────────────────────

def make_save_callback():
    """Returns a callback that saves all tenders to CSV/JSON/SQLite on every call."""
    call_count = [0]

    def save_now(portal_id: str, tenders: list[dict]):
        call_count[0] += 1
        if not tenders:
            return

        # CSV (overwrite — always has latest full set)
        csv_path = OUTPUT_DIR / f"{portal_id}_tenders.csv"
        # We overwrite rather than append to avoid duplicates
        import csv as csv_mod
        from core.storage import FULL_FIELDS, _normalise
        rows = [_normalise(t) for t in tenders]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv_mod.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        # JSON (overwrite)
        save_json(tenders, portal_id, OUTPUT_DIR)

        # SQLite (INSERT OR REPLACE — safe for incremental)
        save_sqlite(tenders, OUTPUT_DIR)

        log.info(
            f"[save] #{call_count[0]}: {len(tenders)} tenders → "
            f"{csv_path.name}, {portal_id}_tenders.json, tenders.db"
        )

    return save_now


# ── Main ─────────────────────────────────────────────────────────────────────

async def run(args):
    portal_id = "karnataka_kppp"
    cfg = PORTALS[portal_id]

    log.info("=" * 70)
    log.info(f"Karnataka KPPP Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Tabs: {args.tabs or 'all'} | Max pages/tab: {args.max_pages or 'UNLIMITED'}")
    log.info(f"Scope: {args.scope} | Fetch details: {not args.no_details} | Headless: {not args.headed}")
    log.info("=" * 70)

    save_callback = make_save_callback()

    async with BrowserSession(headless=not args.headed) as session:
        agent = KPPPAgent(cfg, session)

        tabs = args.tabs.split(",") if args.tabs else None

        result = await agent.scrape(
            max_pages=args.max_pages,
            fetch_details=not args.no_details,
            tabs=tabs,
            save_callback=save_callback,
            scope=args.scope,
        )

    # Classify tenders by industry
    if result.tenders:
        log.info("Classifying tenders by industry category...")
        for t in result.tenders:
            text = " ".join(filter(None, [
                t.get("title", ""),
                t.get("organisation", ""),
                t.get("work_description", ""),
            ]))
            t["industry_category"] = classify_tender_local(text)
            t["source_portal_type"] = "government"
            t["scrape_date"] = datetime.now().strftime("%Y-%m-%d")
            t["scope_scraped"] = args.scope

    # Final save
    if result.tenders:
        save_callback(portal_id, result.tenders)
        save_daily_snapshot(result.tenders, portal_id)
        combined_path = save_combined_csv(result.tenders, OUTPUT_DIR)
        log.info(f"Combined CSV: {combined_path}")

    # Summary
    log.info("")
    log.info("=" * 70)
    log.info("SCRAPE COMPLETE")
    log.info(f"  Total tenders: {len(result.tenders)}")
    log.info(f"  Pages scraped: {result.pages}")
    log.info(f"  Errors:        {len(result.errors)}")
    if result.errors:
        for err in result.errors[:5]:
            log.info(f"    - {err}")
    log.info(f"  Output dir:    {OUTPUT_DIR.resolve()}")
    log.info(f"  CSV:           {OUTPUT_DIR / f'{portal_id}_tenders.csv'}")
    log.info(f"  JSON:          {OUTPUT_DIR / f'{portal_id}_tenders.json'}")
    log.info(f"  SQLite:        {OUTPUT_DIR / 'tenders.db'}")
    log.info("=" * 70)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Karnataka KPPP tender portal"
    )
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Max pages to scrape per tab (default: all)"
    )
    parser.add_argument(
        "--tabs", type=str, default=None,
        help="Comma-separated tabs: goods,works,services (default: all)"
    )
    parser.add_argument(
        "--no-details", action="store_true",
        help="Skip clicking into each tender for detail view"
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Show browser window (useful for debugging)"
    )
    parser.add_argument(
        "--org-filter", type=str, default=None,
        help="Filter by organisation name (case-insensitive substring)"
    )
    parser.add_argument(
        "--scope", type=str, default="all",
        choices=["active", "archive", "awards", "both", "all"],
        help="What to scrape: active, archive, awards, both, or all (default: all)"
    )

    args = parser.parse_args()

    try:
        result = asyncio.run(run(args))
        sys.exit(0 if not result.errors else 1)
    except KeyboardInterrupt:
        log.info("\nInterrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
