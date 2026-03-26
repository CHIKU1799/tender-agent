"""
Tender Agent — Multi-portal Government Tender Scraper
Entry point: python main.py

Supports 22 portals across Central Govt, PSUs, States, and Info portals.
"""
from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.live import Live

# ── Local imports ──────────────────────────────────────────────────────────────
from portals.configs import PORTALS
from agents.gepnic   import GePNICAgent
from agents.gem      import GeMAgent
from agents.ireps    import IREPSAgent
from agents.generic  import GenericAgent
from agents.base     import ScrapeResult
from core.browser    import BrowserSession
from core.storage    import (
    SnapshotStore, save_csv, save_json, save_sqlite,
    write_run_log, OUTPUT_DIR,
)
from interface.cli   import (
    console, build_progress,
    select_portals, configure_filters, confirm_start,
    show_results_summary, show_new_tenders_detail,
)

# ── Logging ────────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main")


# ── Agent factory ──────────────────────────────────────────────────────────────

def make_agent(portal_id: str, session: BrowserSession):
    cfg = PORTALS[portal_id]
    match cfg.platform:
        case "gepnic":
            return GePNICAgent(cfg, session)
        case "gem_api":
            return GeMAgent(cfg)
        case "ireps":
            return IREPSAgent(cfg, session)
        case _:
            return GenericAgent(cfg, session)


# ── Core scrape runner ─────────────────────────────────────────────────────────

async def run_portals(
    portal_ids: list[str],
    filters: dict,
    progress,
    task_map: dict,
) -> dict[str, ScrapeResult]:
    """Run agents for each selected portal, update Rich progress."""
    all_results: dict[str, ScrapeResult] = {}

    async with BrowserSession() as session:
        for portal_id in portal_ids:
            cfg = PORTALS[portal_id]
            task_id = task_map[portal_id]

            async def progress_cb(page_num: int, tender_count: int, _tid=task_id):
                progress.update(_tid, completed=page_num, tenders=tender_count)

            progress.update(task_id, description=f"{cfg.emoji} {cfg.display_name}")
            agent = make_agent(portal_id, session)

            try:
                result = await agent.scrape(
                    max_pages=filters.get("max_pages"),
                    org_filter=filters.get("org_filter"),
                    progress_cb=progress_cb,
                )
            except Exception as e:
                log.error(f"Portal {portal_id} failed: {e}")
                result = ScrapeResult(portal_id=portal_id, errors=[str(e)])

            all_results[portal_id] = result
            progress.update(task_id, description=f"[green]✓[/green] {cfg.emoji} {cfg.display_name}")

    return all_results


# ── Save outputs ───────────────────────────────────────────────────────────────

def save_all(
    all_results: dict[str, ScrapeResult],
    filters: dict,
    output_dir: Path = OUTPUT_DIR,
) -> dict[str, int]:
    """Save results in all requested formats. Returns {portal_id: new_count}."""
    snapshot  = SnapshotStore()
    new_counts: dict[str, int] = {}
    log_entries = []

    for portal_id, result in all_results.items():
        if not result.tenders:
            new_counts[portal_id] = 0
            continue

        tenders = result.tenders

        # Diff against snapshot
        new = snapshot.diff(portal_id, tenders)
        new_counts[portal_id] = len(new)

        # Save
        fmts = filters.get("export_formats", ["csv", "json"])
        if "csv"    in fmts: save_csv(tenders, portal_id, output_dir)
        if "json"   in fmts: save_json(tenders, portal_id, output_dir)
        if "sqlite" in fmts: save_sqlite(tenders, output_dir)

        # Update snapshot
        snapshot.save(portal_id, tenders)

        log_entries.append({
            "portal_id": portal_id,
            "total":     len(tenders),
            "new":       len(new),
            "pages":     result.pages,
        })

    write_run_log(log_entries)
    return new_counts


def collect_new_tenders(
    all_results: dict[str, ScrapeResult],
    new_counts: dict[str, int],
) -> list[dict]:
    """Build flat list of only new tenders across all portals."""
    snap     = SnapshotStore()
    new_list = []
    for portal_id, result in all_results.items():
        if not result.tenders:
            continue
        known_ids = snap.load_known_ids(portal_id)
        for t in result.tenders:
            uid = t.get("tender_id") or t.get("detail_url", "")
            if uid not in known_ids:
                new_list.append(t)
    return new_list


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # ── Step 1: Portal selection ───────────────────────────────────────────────
    portal_ids = await select_portals()
    if not portal_ids:
        return

    # ── Step 2 & 3: Filters + export formats ──────────────────────────────────
    filters = await configure_filters()

    # ── Step 4: Confirm and start ──────────────────────────────────────────────
    if not await confirm_start(portal_ids, filters):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    # ── Step 5: Run with live progress display ─────────────────────────────────
    progress  = build_progress()
    task_map  = {}
    max_p     = filters.get("max_pages") or 999

    for pid in portal_ids:
        cfg = PORTALS[pid]
        task_map[pid] = progress.add_task(
            f"{cfg.emoji} {cfg.display_name}",
            total=max_p,
            tenders=0,
        )

    console.print()
    all_results: dict[str, ScrapeResult] = {}

    with Live(progress, console=console, refresh_per_second=4):
        all_results = await run_portals(portal_ids, filters, progress, task_map)

    # ── Step 6: Save outputs + diff ────────────────────────────────────────────
    new_counts   = save_all(all_results, filters)
    new_tenders  = collect_new_tenders(all_results, new_counts)

    # ── Step 7: Show summary ───────────────────────────────────────────────────
    show_results_summary(all_results, new_counts, OUTPUT_DIR)

    if new_tenders:
        show_new_tenders_detail(new_tenders)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(0)
