"""
Tender Agent — Multi-portal Government Tender Scraper
Entry point: python main.py
"""
from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path

from rich.live import Live

from portals.configs import PORTALS
from agents.gepnic    import GePNICAgent
from agents.gem       import GeMAgent
from agents.ireps     import IREPSAgent
from agents.generic   import GenericAgent
from agents.cppp      import CPPPAgent
from agents.karnataka import KarnatakaAgent
from agents.base    import ScrapeResult
from core.browser   import BrowserSession
from core.storage   import (
    SnapshotStore, save_csv, save_json, save_sqlite,
    save_combined_csv, save_awards_csv, write_run_log, OUTPUT_DIR,
)
from interface.cli  import (
    console, build_progress,
    select_portals, configure_filters, confirm_start,
    show_results_summary, show_new_tenders_detail,
)

# ── Logging ───────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
Path("screenshots").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.FileHandler("logs/agent.log", encoding="utf-8")],
)
log = logging.getLogger("main")


# ── Agent factory ─────────────────────────────────────────────────────────────

def make_agent(portal_id: str, session: BrowserSession):
    cfg = PORTALS[portal_id]
    if   cfg.platform == "gepnic":          return GePNICAgent(cfg, session)
    elif cfg.platform == "gem_api":         return GeMAgent(cfg, session)
    elif cfg.platform == "ireps":           return IREPSAgent(cfg, session)
    elif cfg.platform == "cppp":            return CPPPAgent(cfg, session)
    elif cfg.platform == "karnataka_seam":  return KarnatakaAgent(cfg, session)
    else:                                   return GenericAgent(cfg, session)


# ── Run portals ───────────────────────────────────────────────────────────────

async def run_portals(
    portal_ids: list[str],
    filters:    dict,
    progress,
    task_map:   dict,
) -> dict[str, ScrapeResult]:

    all_results: dict[str, ScrapeResult] = {}

    async with BrowserSession() as session:
        for portal_id in portal_ids:
            cfg     = PORTALS[portal_id]
            task_id = task_map[portal_id]

            progress.update(task_id, description=f"{cfg.emoji}  Scraping {cfg.display_name}...")

            async def progress_cb(page_num: int, count: int, _tid=task_id):
                progress.update(_tid, completed=page_num, tenders=count)

            agent = make_agent(portal_id, session)
            try:
                result = await agent.scrape(
                    max_pages=filters.get("max_pages"),
                    org_filter=filters.get("org_filter"),
                    fetch_details=filters.get("fetch_details", False),
                    progress_cb=progress_cb,
                )
            except Exception as e:
                log.error(f"Portal {portal_id} crashed: {e}")
                result = ScrapeResult(portal_id=portal_id, errors=[str(e)])

            all_results[portal_id] = result

            status = "✓" if not result.errors and not result.skipped else ("⚠" if result.skipped else "✗")
            progress.update(
                task_id,
                description=f"[{'green' if status=='✓' else 'yellow'}]{status}[/]  {cfg.emoji}  {cfg.display_name}",
                completed=result.pages,
                tenders=len(result.tenders),
            )

    return all_results


# ── Save all outputs ──────────────────────────────────────────────────────────

def save_all(
    all_results: dict[str, ScrapeResult],
    filters:     dict,
    output_dir:  Path = OUTPUT_DIR,
) -> tuple[dict[str, int], list[dict]]:
    """
    Save per-portal files + combined CSV.
    Returns (new_counts_by_portal, flat_list_of_new_tenders).
    """
    snapshot    = SnapshotStore()
    new_counts: dict[str, int] = {}
    all_new:    list[dict]     = []
    log_entries: list[dict]    = []
    all_tenders: list[dict]    = []

    fmts = filters.get("export_formats", ["csv", "json"])

    for portal_id, result in all_results.items():
        if not result.tenders:
            new_counts[portal_id] = 0
            continue

        tenders = result.tenders
        all_tenders.extend(tenders)

        # Diff against previous snapshot
        new_tenders = snapshot.diff(portal_id, tenders)
        new_counts[portal_id] = len(new_tenders)
        all_new.extend(new_tenders)

        # Per-portal files
        if "csv"    in fmts: save_csv(tenders, portal_id, output_dir)
        if "json"   in fmts: save_json(tenders, portal_id, output_dir)
        if "sqlite" in fmts: save_sqlite(tenders, output_dir)

        snapshot.save(portal_id, tenders)

        log_entries.append({
            "portal_id": portal_id,
            "total":     len(tenders),
            "new":       len(new_tenders),
            "pages":     result.pages,
        })

    # Combined CSV across all portals
    if all_tenders and "csv" in fmts:
        combined_path = save_combined_csv(all_tenders, output_dir)
        log.info(f"Combined CSV: {combined_path} ({len(all_tenders)} rows)")

        # Awards CSV — only tenders with award/winner data
        awards_path = save_awards_csv(all_tenders, output_dir)
        if awards_path:
            awarded_count = len([t for t in all_tenders if t.get("award_winner") or t.get("award_date")])
            log.info(f"Awards CSV: {awards_path} ({awarded_count} awarded tenders)")

    write_run_log(log_entries)
    return new_counts, all_new


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Step 1: Select portals
    portal_ids = await select_portals()
    if not portal_ids:
        return

    # Steps 2–3: Filters + export formats
    filters = await configure_filters()

    # Step 4: Confirm
    if not await confirm_start(portal_ids, filters):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # Step 5: Run with live progress
    progress = build_progress()
    task_map = {}
    max_p    = filters.get("max_pages") or 9999

    for pid in portal_ids:
        cfg = PORTALS[pid]
        task_map[pid] = progress.add_task(
            f"{cfg.emoji}  {cfg.display_name}",
            total=max_p,
            tenders=0,
        )

    console.print()
    with Live(progress, console=console, refresh_per_second=4):
        all_results = await run_portals(portal_ids, filters, progress, task_map)

    # Step 6: Save + diff
    new_counts, new_tenders = save_all(all_results, filters)

    # Step 7: Display summary
    show_results_summary(all_results, new_counts, OUTPUT_DIR)
    if new_tenders:
        show_new_tenders_detail(new_tenders)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
