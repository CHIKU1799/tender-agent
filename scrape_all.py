#!/usr/bin/env python3
"""
Full Scrape — All Working Portals, All Pages, All Details
═══════════════════════════════════════════════════════════
Runs every accessible portal end-to-end and saves everything.

Usage:
    python scrape_all.py                    # full scrape, all pages
    python scrape_all.py --max-pages 10     # cap at 10 pages per portal
    python scrape_all.py --headed           # show browser
"""
from __future__ import annotations

import argparse
import asyncio
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
Path("output/daily").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/full_scrape.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("scrape_all")

from portals.configs import PORTALS
from agents.gepnic    import GePNICAgent
from agents.gepnic_archive import GePNICArchiveAgent
from agents.gem       import GeMAgent
from agents.ireps     import IREPSAgent
from agents.cppp      import CPPPAgent
from agents.kppp      import KPPPAgent
from agents.aggregator import get_aggregator_agent
from agents.base      import ScrapeResult
from core.browser     import BrowserSession
from core.storage     import (
    save_csv, save_json, save_sqlite,
    save_combined_csv, save_awards_csv, save_daily_snapshot,
    get_portal_stats, get_category_breakdown, get_total_stats,
    OUTPUT_DIR,
)
from ai.classifier import classify_tender_local


# ── Portal tiers ─────────────────────────────────────────────────────────────
# Tier 1: Proven portals — always scrape
TIER1_PORTALS = [
    # Central
    "gem", "cppp", "ireps",
    # GePNIC — Central & PSU
    "defproc", "etenders", "ntpc", "coalindia",
    # GePNIC — State (original)
    "maharashtra", "up", "tamilnadu", "gujarat", "rajasthan",
    "delhi", "mp", "uttarakhand",
    # Karnataka
    "karnataka_kppp",
    # Aggregator (public)
    "tenderdetail",
]

# Tier 2: New GePNIC state portals from vansh branch — should work (same platform)
TIER2_GEPNIC = [
    pid for pid, cfg in PORTALS.items()
    if cfg.platform == "gepnic" and pid not in TIER1_PORTALS
]

# Tier 3: New aggregators and special agents from vansh
TIER3_NEW = [
    pid for pid, cfg in PORTALS.items()
    if pid not in TIER1_PORTALS and pid not in TIER2_GEPNIC
    and cfg.platform not in ("generic",)  # skip generic/info portals that need manual work
    and cfg.category not in ("Info", "Municipal")
]

# Combined working list
WORKING_PORTALS = TIER1_PORTALS + TIER2_GEPNIC + TIER3_NEW
log.info(f"Portal tiers: T1={len(TIER1_PORTALS)} T2={len(TIER2_GEPNIC)} T3={len(TIER3_NEW)} Total={len(WORKING_PORTALS)}")


def make_agent(portal_id: str, session: BrowserSession, scope: str = "active"):
    """Create the right agent for each portal."""
    cfg = PORTALS[portal_id]

    # Archive/awards for GePNIC portals
    if scope != "active" and cfg.platform == "gepnic":
        return GePNICArchiveAgent(cfg, session, scope=scope)

    if cfg.platform == "gepnic":
        return GePNICAgent(cfg, session)
    elif cfg.platform == "gem_api":
        return GeMAgent(cfg, session)
    elif cfg.platform == "ireps":
        return IREPSAgent(cfg, session)
    elif cfg.platform == "cppp":
        return CPPPAgent(cfg, session)
    elif cfg.platform == "karnataka_kppp":
        return KPPPAgent(cfg, session)
    elif cfg.platform == "aggregator":
        return get_aggregator_agent(portal_id, session)
    return None


async def scrape_portal(
    portal_id: str, session: BrowserSession,
    max_pages: int | None, fetch_details: bool,
    scope: str = "active",
) -> ScrapeResult:
    """Scrape a single portal with full error handling."""
    cfg = PORTALS[portal_id]
    agent = make_agent(portal_id, session, scope=scope)
    if agent is None:
        return ScrapeResult(portal_id=portal_id, skipped=True,
                            skip_reason="No agent available")

    kwargs = {"max_pages": max_pages, "fetch_details": fetch_details}

    # KPPP supports scope parameter for archive/awarded
    if cfg.platform == "karnataka_kppp":
        kwargs["save_callback"] = lambda pid, t: None  # we save after
        kwargs["scope"] = scope

    result = await agent.scrape(**kwargs)
    return result


# ── GePNIC portals that have archive/awards URLs ────────────────────────────
ARCHIVE_PORTALS = [
    pid for pid, cfg in PORTALS.items()
    if cfg.platform == "gepnic" and (cfg.archive_url or cfg.awards_url)
]


async def _scrape_one(
    portal_id: str, session: BrowserSession,
    max_pages: int | None, fetch_details: bool,
    scope: str, all_results: dict, all_tenders: list,
    portal_timings: dict, label: str,
):
    """Scrape a single portal, classify, tag, save incrementally."""
    cfg = PORTALS[portal_id]
    log.info(f"\n  [{label}] {cfg.emoji}  {cfg.display_name}  (scope={scope})")
    log.info(f"    URL: {cfg.base_url}")

    portal_start = time.time()
    try:
        result = await scrape_portal(
            portal_id, session,
            max_pages=max_pages,
            fetch_details=fetch_details,
            scope=scope,
        )
    except Exception as e:
        log.error(f"    CRASHED: {e}")
        result = ScrapeResult(portal_id=portal_id, errors=[str(e)])

    elapsed = time.time() - portal_start
    key = f"{portal_id}_{scope}"
    portal_timings[key] = elapsed
    all_results[key] = result

    # Classify and tag
    for t in result.tenders:
        text = " ".join(filter(None, [
            t.get("title", ""),
            t.get("organisation", ""),
            t.get("work_description", ""),
        ]))
        t["industry_category"] = classify_tender_local(text)
        t["source_portal_type"] = (
            "aggregator" if cfg.category == "Aggregator" else "government"
        )
        t["scrape_date"] = datetime.now().strftime("%Y-%m-%d")

    all_tenders.extend(result.tenders)

    status = "OK" if not result.errors and not result.skipped else (
        f"SKIP: {result.skip_reason}" if result.skipped else
        f"ERR: {result.errors[-1][:60]}" if result.errors else "?"
    )
    log.info(
        f"    → {len(result.tenders)} tenders | {result.pages} pages | "
        f"{elapsed:.1f}s | {status}"
    )

    # Incremental save
    if result.tenders:
        save_csv(result.tenders, portal_id, OUTPUT_DIR)
        save_json(result.tenders, portal_id, OUTPUT_DIR)
        save_sqlite(result.tenders, OUTPUT_DIR)
        save_daily_snapshot(result.tenders, portal_id)


async def run(args):
    start_time = time.time()
    max_pages = args.max_pages
    fetch_details = not args.no_details
    headless = not args.headed
    scope = args.scope

    log.info("=" * 70)
    log.info(f"FULL SCRAPE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Portals: {len(WORKING_PORTALS)} | Max pages: {max_pages or 'ALL'}")
    log.info(f"Scope: {scope} | Fetch details: {fetch_details} | Headless: {headless}")
    log.info("=" * 70)

    all_results: dict[str, ScrapeResult] = {}
    all_tenders: list[dict] = []
    portal_timings: dict[str, float] = {}

    async with BrowserSession(headless=headless) as session:
        # ── PASS 1: Active tenders from all working portals ──────────────
        if scope in ("active", "all"):
            log.info("\n" + "=" * 70)
            log.info("PASS 1: ACTIVE TENDERS")
            log.info("=" * 70)
            for i, portal_id in enumerate(WORKING_PORTALS, 1):
                label = f"{i}/{len(WORKING_PORTALS)}"
                await _scrape_one(
                    portal_id, session, max_pages, fetch_details,
                    "active", all_results, all_tenders, portal_timings, label,
                )

        # ── PASS 2: Archive + Awarded from KPPP ─────────────────────────
        if scope in ("archive", "awards", "both", "all"):
            kppp_scope = "both" if scope == "all" else scope
            log.info("\n" + "=" * 70)
            log.info(f"PASS 2: KARNATAKA KPPP — {kppp_scope.upper()} TENDERS")
            log.info("=" * 70)
            await _scrape_one(
                "karnataka_kppp", session, max_pages, fetch_details,
                kppp_scope, all_results, all_tenders, portal_timings,
                "KPPP-archive",
            )

        # ── PASS 3: Archive + Awarded from GePNIC portals ───────────────
        if scope in ("archive", "awards", "both", "all"):
            gepnic_scope = "both" if scope == "all" else scope
            log.info("\n" + "=" * 70)
            log.info(f"PASS 3: GePNIC ARCHIVE/AWARDS — {gepnic_scope.upper()}")
            log.info(f"Portals with archive URLs: {len(ARCHIVE_PORTALS)}")
            log.info("=" * 70)
            for i, portal_id in enumerate(ARCHIVE_PORTALS, 1):
                label = f"ARC {i}/{len(ARCHIVE_PORTALS)}"
                await _scrape_one(
                    portal_id, session, max_pages, False,  # skip details for archive (no detail links)
                    gepnic_scope, all_results, all_tenders, portal_timings, label,
                )

    # ── Final combined save ──────────────────────────────────────────────────
    if all_tenders:
        combined_path = save_combined_csv(all_tenders, OUTPUT_DIR)
        awards_path = save_awards_csv(all_tenders, OUTPUT_DIR)
        log.info(f"\nCombined CSV: {combined_path} ({len(all_tenders)} rows)")
        if awards_path:
            awarded = len([t for t in all_tenders
                           if t.get("award_winner") or t.get("award_date")])
            log.info(f"Awards CSV: {awards_path} ({awarded} awarded)")

    # ── Summary ──────────────────────────────────────────────────────────────
    total_time = time.time() - start_time

    log.info("")
    log.info("=" * 70)
    log.info("SCRAPE COMPLETE")
    log.info("=" * 70)
    log.info(f"Total time:    {total_time:.0f}s ({total_time/60:.1f} min)")
    log.info(f"Total tenders: {len(all_tenders)}")
    log.info("")

    # Per-portal summary
    log.info(f"{'Portal (scope)':<35s} {'Tenders':>8s} {'Pages':>6s} {'Time':>7s} {'Status'}")
    log.info("-" * 80)
    total_t = 0
    for key, r in all_results.items():
        pid = key.rsplit("_", 1)[0] if "_" in key else key
        cfg = PORTALS.get(pid)
        if not cfg:
            continue
        t_count = len(r.tenders)
        total_t += t_count
        elapsed = portal_timings.get(key, 0)
        if r.skipped:
            st = f"⚠ {r.skip_reason[:30]}"
        elif r.errors:
            st = f"✗ {r.errors[-1][:30]}"
        else:
            st = "✓"
        log.info(f"{cfg.emoji} {key:<32s} {t_count:>8d} {r.pages:>6d} {elapsed:>6.1f}s {st}")
    log.info("-" * 80)
    log.info(f"{'TOTAL':<35s} {total_t:>8d}")

    # Category breakdown
    cats = Counter(t.get("industry_category", "Other") for t in all_tenders)
    log.info("")
    log.info("Industry Categories:")
    for cat, cnt in cats.most_common():
        pct = 100 * cnt / max(len(all_tenders), 1)
        bar = "█" * int(pct / 2)
        log.info(f"  {cat:<35s} {cnt:>5d} ({pct:4.1f}%) {bar}")

    # Output files
    log.info("")
    log.info("Output files:")
    for f in sorted(OUTPUT_DIR.glob("*.*")):
        size_kb = f.stat().st_size // 1024
        log.info(f"  {f}  ({size_kb} KB)")

    log.info("")
    log.info(f"Daily snapshot: output/daily/{datetime.now().strftime('%Y-%m-%d')}/")
    log.info(f"Dashboard:      python -m interface.dashboard.app")
    log.info("=" * 70)

    return all_results, all_tenders


def main():
    parser = argparse.ArgumentParser(description="Full scrape — all portals, all pages, all scopes")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Max pages per portal (default: ALL)")
    parser.add_argument("--no-details", action="store_true",
                        help="Skip detail page scraping (faster)")
    parser.add_argument("--headed", action="store_true",
                        help="Show browser window")
    parser.add_argument("--scope", type=str, default="all",
                        choices=["active", "archive", "awards", "both", "all"],
                        help="What to scrape: active, archive, awards, both, or all (default: all)")
    args = parser.parse_args()

    try:
        results, tenders = asyncio.run(run(args))
        errors = sum(1 for r in results.values() if r.errors)
        sys.exit(1 if errors > len(results) // 2 else 0)
    except KeyboardInterrupt:
        log.info("\nInterrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
