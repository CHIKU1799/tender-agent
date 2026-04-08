#!/usr/bin/env python3
"""
CPPP Full Scraper — All 2700+ Pages, All Details, All Fields
═════════════════════════════════════════════════════════════
Scrapes the entire CPPP active tender listing + detail pages.

Strategy:
  Phase 1: Listing pages — fast sequential, ~8s/page, saved every 50 pages
  Phase 2: Detail pages — 4 parallel tabs, ~2s/detail, saved every 100 details

Resume: Automatically skips already-scraped tender IDs from existing CSV.

Usage:
    python scrape_cppp_full.py                      # full scrape
    python scrape_cppp_full.py --max-pages 100      # first 100 pages only
    python scrape_cppp_full.py --skip-details        # listings only (fast)
    python scrape_cppp_full.py --resume              # skip existing tender IDs
    python scrape_cppp_full.py --detail-workers 6    # more parallel detail tabs
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import Counter

from dotenv import load_dotenv
load_dotenv()

# ── Setup ────────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
Path("screenshots").mkdir(exist_ok=True)
Path("output").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/cppp_full.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("cppp_full")

from portals.configs import PORTALS
from agents.cppp import CPPPAgent, EXTRACT_JS, DETAIL_JS
from agents.base import ScrapeResult
from core.browser import BrowserSession, random_delay
from core.storage import (
    save_csv, save_json, save_sqlite,
    save_combined_csv, save_daily_snapshot,
    OUTPUT_DIR, FULL_FIELDS, _normalise,
)
from core.utils import now_iso
from ai.classifier import classify_tender_local


def load_existing_ids() -> set[str]:
    """Load tender IDs already scraped from existing CPPP CSV."""
    path = OUTPUT_DIR / "cppp_tenders.csv"
    ids = set()
    if path.exists():
        try:
            with open(path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    tid = row.get("tender_id", "").strip()
                    if tid:
                        ids.add(tid)
        except Exception:
            pass
    log.info(f"[resume] Found {len(ids)} existing tender IDs in CSV")
    return ids


def save_incremental(tenders: list[dict], label: str):
    """Save current state to all formats."""
    if not tenders:
        return
    # Classify
    for t in tenders:
        if not t.get("industry_category"):
            text = " ".join(filter(None, [
                t.get("title", ""),
                t.get("organisation", ""),
                t.get("work_description", ""),
            ]))
            t["industry_category"] = classify_tender_local(text)
        t.setdefault("source_portal_type", "government")
        t.setdefault("scrape_date", datetime.now().strftime("%Y-%m-%d"))

    save_csv(tenders, "cppp", OUTPUT_DIR)
    save_json(tenders, "cppp", OUTPUT_DIR)
    save_sqlite(tenders, OUTPUT_DIR)
    log.info(f"[save] {label}: {len(tenders)} tenders → CSV/JSON/SQLite")


async def phase1_listings(
    session: BrowserSession,
    max_pages: int | None,
    existing_ids: set[str],
    resume: bool,
) -> list[dict]:
    """Phase 1: Scrape all listing pages. Fast, no detail clicks."""
    log.info("=" * 70)
    log.info("PHASE 1: LISTING PAGES")
    log.info("=" * 70)

    ctx = await session.new_context()
    page = await session.new_page(ctx)
    all_tenders = []
    current_page = 1
    empty_streak = 0
    start = time.time()

    try:
        while True:
            if max_pages and current_page > max_pages:
                break

            url = f"https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata?page={current_page}"
            try:
                await page.goto(url, wait_until="networkidle", timeout=60_000)
                await page.wait_for_timeout(1500)
            except Exception as e:
                log.warning(f"[cppp] Page {current_page} load failed: {e}")
                empty_streak += 1
                if empty_streak >= 3:
                    break
                await asyncio.sleep(5)
                current_page += 1
                continue

            data = await page.evaluate(EXTRACT_JS)
            rows = data.get("rows", [])

            if not rows:
                empty_streak += 1
                if empty_streak >= 3:
                    log.info(f"[cppp] 3 consecutive empty pages at page {current_page} — done")
                    break
                current_page += 1
                await asyncio.sleep(2)
                continue

            empty_streak = 0

            for r in rows:
                title = r.get("title", "")
                ref_num = r.get("ref_no", "")
                tender_id = r.get("tender_id", "") or ref_num or title[:80]

                # Skip if resume and already scraped
                if resume and tender_id in existing_ids:
                    continue

                tender = {
                    "portal_id":       "cppp",
                    "portal_name":     "Central Public Procurement Portal (CPPP)",
                    "tender_id":       tender_id,
                    "ref_number":      ref_num,
                    "title":           title,
                    "organisation":    r.get("organisation", ""),
                    "published_date":  r.get("published_date", ""),
                    "closing_date":    r.get("closing_date", ""),
                    "opening_date":    r.get("opening_date", ""),
                    "status":          "Active",
                    "detail_url":      r.get("detail_href", ""),
                    "scraped_at":      now_iso(),
                    "page_num":        current_page,
                    "detail_scraped":  "False",
                }
                all_tenders.append(tender)

            elapsed = time.time() - start
            rate = current_page / max(elapsed, 1) * 60
            log.info(
                f"[cppp] Page {current_page} — {len(rows)} rows "
                f"(total: {len(all_tenders)}) "
                f"[{rate:.0f} pages/min]"
            )

            # Save every 50 pages
            if current_page % 50 == 0:
                save_incremental(all_tenders, f"page {current_page}")

            current_page += 1
            await random_delay(0.8, 2.0)

    except Exception as e:
        log.error(f"[cppp] Phase 1 error at page {current_page}: {e}")
    finally:
        await ctx.close()

    # Final save for phase 1
    save_incremental(all_tenders, "phase1-complete")

    elapsed = time.time() - start
    log.info(f"\n[cppp] Phase 1 complete: {len(all_tenders)} tenders from {current_page-1} pages in {elapsed:.0f}s")
    return all_tenders


async def phase2_details(
    session: BrowserSession,
    tenders: list[dict],
    workers: int = 4,
    existing_ids: set[str] = set(),
) -> list[dict]:
    """Phase 2: Fetch detail pages with parallel workers."""
    # Filter to only tenders that need details
    need_details = [
        (i, t) for i, t in enumerate(tenders)
        if t.get("detail_url") and t.get("detail_scraped") != "True"
    ]

    if not need_details:
        log.info("[cppp] No detail pages to fetch")
        return tenders

    log.info("=" * 70)
    log.info(f"PHASE 2: DETAIL PAGES ({len(need_details)} tenders, {workers} workers)")
    log.info("=" * 70)

    ctx = await session.new_context()
    q = asyncio.Queue()
    for item in need_details:
        await q.put(item)

    completed = [0]
    errors = [0]
    start = time.time()

    agent = CPPPAgent(PORTALS["cppp"], session)

    async def worker(wid: int):
        page = await session.new_page(ctx)
        try:
            while not q.empty():
                try:
                    idx, t = q.get_nowait()
                except asyncio.QueueEmpty:
                    break

                url = t.get("detail_url", "")
                try:
                    await page.goto(url, wait_until="networkidle", timeout=45_000)
                    await page.wait_for_timeout(800)
                    data = await page.evaluate(DETAIL_JS)
                    merged = agent._merge_detail(t, data)
                    tenders[idx] = merged
                    completed[0] += 1
                except Exception as e:
                    log.warning(f"[W{wid}] Detail failed {t.get('tender_id','?')}: {e}")
                    tenders[idx]["detail_scraped"] = "Error"
                    errors[0] += 1

                total_done = completed[0] + errors[0]
                if total_done % 20 == 0:
                    elapsed = time.time() - start
                    rate = total_done / max(elapsed, 1) * 60
                    log.info(
                        f"[cppp] Details: {completed[0]} OK / {errors[0]} err "
                        f"of {len(need_details)} "
                        f"[{rate:.0f}/min]"
                    )

                # Save every 200 details
                if total_done % 200 == 0:
                    save_incremental(tenders, f"details-{total_done}")

                await asyncio.sleep(0.8)
        finally:
            await page.close()

    tasks = [asyncio.create_task(worker(i)) for i in range(min(workers, len(need_details)))]
    await asyncio.gather(*tasks)
    await ctx.close()

    # Final save
    save_incremental(tenders, "phase2-complete")

    elapsed = time.time() - start
    log.info(
        f"\n[cppp] Phase 2 complete: {completed[0]} details fetched, "
        f"{errors[0]} errors in {elapsed:.0f}s"
    )
    return tenders


async def run(args):
    start_time = time.time()

    log.info("=" * 70)
    log.info(f"CPPP FULL SCRAPE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Max pages: {args.max_pages or 'ALL'}")
    log.info(f"Detail workers: {args.detail_workers}")
    log.info(f"Skip details: {args.skip_details}")
    log.info(f"Resume: {args.resume}")
    log.info("=" * 70)

    existing_ids = load_existing_ids() if args.resume else set()

    async with BrowserSession(headless=not args.headed) as session:
        # Phase 1: Listings
        tenders = await phase1_listings(
            session, args.max_pages, existing_ids, args.resume,
        )

        # Phase 2: Details
        if not args.skip_details and tenders:
            tenders = await phase2_details(
                session, tenders,
                workers=args.detail_workers,
                existing_ids=existing_ids,
            )

    # Final combined save + daily snapshot
    if tenders:
        save_incremental(tenders, "final")
        save_daily_snapshot(tenders, "cppp")
        combined = save_combined_csv(tenders, OUTPUT_DIR)
        log.info(f"Combined CSV: {combined}")

    # Summary
    total_time = time.time() - start_time
    with_details = sum(1 for t in tenders if t.get("detail_scraped") == "True")
    cats = Counter(t.get("industry_category", "Other") for t in tenders)

    log.info("")
    log.info("=" * 70)
    log.info("CPPP FULL SCRAPE COMPLETE")
    log.info("=" * 70)
    log.info(f"Total time:     {total_time:.0f}s ({total_time/60:.1f} min)")
    log.info(f"Total tenders:  {len(tenders)}")
    log.info(f"With details:   {with_details}")
    log.info(f"Detail rate:    {100*with_details/max(len(tenders),1):.1f}%")
    log.info("")

    log.info("Industry Categories:")
    for cat, cnt in cats.most_common(15):
        pct = 100 * cnt / max(len(tenders), 1)
        bar = "█" * int(pct / 2)
        log.info(f"  {cat:<35s} {cnt:>6d} ({pct:4.1f}%) {bar}")

    log.info("")
    log.info("Output files:")
    for f in sorted(OUTPUT_DIR.glob("cppp*")):
        size_kb = f.stat().st_size // 1024
        log.info(f"  {f}  ({size_kb} KB)")
    log.info("=" * 70)

    return tenders


def main():
    parser = argparse.ArgumentParser(description="CPPP full scrape — all pages, all details")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Max listing pages (default: ALL ~2774)")
    parser.add_argument("--skip-details", action="store_true",
                        help="Only scrape listings, skip detail pages")
    parser.add_argument("--detail-workers", type=int, default=4,
                        help="Parallel detail page workers (default: 4)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip tender IDs already in existing CSV")
    parser.add_argument("--headed", action="store_true",
                        help="Show browser window")
    args = parser.parse_args()

    try:
        tenders = asyncio.run(run(args))
        log.info(f"Exit: {len(tenders)} tenders scraped")
        sys.exit(0)
    except KeyboardInterrupt:
        log.info("\nInterrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
