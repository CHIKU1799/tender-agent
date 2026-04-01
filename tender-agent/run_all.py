"""
Run ALL portals — max 2 pages each — save to output/ and print summary.
Usage: python3 run_all.py
"""
from __future__ import annotations
import asyncio, sys, logging
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

Path("logs").mkdir(exist_ok=True)
Path("output").mkdir(exist_ok=True)
Path("screenshots").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/run_all.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("run_all")

from core.browser import BrowserSession
from core.storage import (
    save_csv, save_json, save_sqlite, save_combined_csv, save_awards_csv,
    SnapshotStore, OUTPUT_DIR,
)
from portals.configs import PORTALS
from agents.gepnic    import GePNICAgent
from agents.gem       import GeMAgent
from agents.ireps     import IREPSAgent
from agents.generic   import GenericAgent
from agents.cppp      import CPPPAgent
from agents.karnataka import KarnatakaAgent


def make_agent(portal_id: str, session: BrowserSession):
    cfg = PORTALS[portal_id]
    if   cfg.platform == "gepnic":          return GePNICAgent(cfg, session)
    elif cfg.platform == "gem_api":         return GeMAgent(cfg, session)
    elif cfg.platform == "ireps":           return IREPSAgent(cfg, session)
    elif cfg.platform == "cppp":            return CPPPAgent(cfg, session)
    elif cfg.platform == "karnataka_seam":  return KarnatakaAgent(cfg, session)
    else:                                   return GenericAgent(cfg, session)


def print_table(tenders, max_rows=8):
    if not tenders:
        print("    (no tenders)")
        return
    W = (50, 28, 12, 12)
    sep = f"+{'-'*(W[0]+2)}+{'-'*(W[1]+2)}+{'-'*(W[2]+2)}+{'-'*(W[3]+2)}+"
    fmt = f"| {{:<{W[0]}}} | {{:<{W[1]}}} | {{:<{W[2]}}} | {{:<{W[3]}}} |"
    print(sep)
    print(fmt.format("Title", "Organisation", "Published", "Closing"))
    print(sep)
    for t in tenders[:max_rows]:
        print(fmt.format(
            (t.get("title") or "")[:W[0]],
            (t.get("organisation") or "")[:W[1]],
            (t.get("published_date") or "")[:W[2]],
            (t.get("closing_date") or "")[:W[3]],
        ))
    print(sep)
    if len(tenders) > max_rows:
        print(f"    … +{len(tenders)-max_rows} more rows")


async def main():
    MAX_PAGES = 2

    all_tenders: list[dict] = []
    summary: list[dict] = []

    print("\n" + "="*75)
    print("  TENDER AGENT — ALL PORTALS RUN")
    print(f"  Portals: {len(PORTALS)}   Max pages each: {MAX_PAGES}")
    print("="*75)

    async with BrowserSession(headless=True) as session:
        for portal_id, cfg in PORTALS.items():
            print(f"\n{'─'*75}")
            print(f"  {cfg.emoji}  [{cfg.category}] {cfg.display_name}")
            print(f"{'─'*75}")

            agent = make_agent(portal_id, session)
            try:
                result = await agent.scrape(max_pages=MAX_PAGES)
            except Exception as e:
                log.error(f"{portal_id} crashed: {e}")
                summary.append({"portal": portal_id, "name": cfg.display_name,
                                 "category": cfg.category, "n": 0, "pages": 0,
                                 "errors": 1, "skipped": False})
                print(f"  ✗  CRASH: {e}")
                continue

            n = len(result.tenders)
            icon = "✓" if n > 0 and not result.errors else ("⚠" if result.skipped else "✗")
            note = result.skip_reason if result.skipped else (str(result.errors[0])[:60] if result.errors else "")

            print(f"  {icon}  {n} tenders, {result.pages} page(s){(' — ' + note) if note else ''}")
            print_table(result.tenders)

            if result.tenders:
                save_csv(result.tenders, portal_id)
                save_json(result.tenders, portal_id)
                save_sqlite(result.tenders)
                all_tenders.extend(result.tenders)

            summary.append({
                "portal":   portal_id,
                "name":     cfg.display_name,
                "category": cfg.category,
                "n":        n,
                "pages":    result.pages,
                "errors":   len(result.errors),
                "skipped":  result.skipped,
            })

    # Combined outputs
    if all_tenders:
        save_combined_csv(all_tenders)
        save_awards_csv(all_tenders)
        SnapshotStore().save("__all__", all_tenders)

    # Print final summary
    print("\n\n" + "="*75)
    print("  FINAL SUMMARY")
    print("="*75)
    print(f"  {'Category':<10} {'Portal':<14} {'Name':<40} {'Tenders':>8}  {'Pages':>5}  St")
    print(f"  {'─'*73}")
    total = 0
    for s in summary:
        icon = "✓" if s["n"] > 0 and not s["errors"] else ("⚠" if s["skipped"] else "✗")
        name = s["name"][:40]
        print(f"  {icon} {s['category']:<10} {s['portal']:<14} {name:<40} {s['n']:>8}  {s['pages']:>5}")
        total += s["n"]
    print(f"  {'─'*73}")
    print(f"  {'TOTAL':<67} {total:>8}")

    print(f"\n  Saved to: output/")
    for s in summary:
        if s["n"] > 0:
            print(f"    • {s['portal']}_tenders.csv  ({s['n']} rows)")
    if all_tenders:
        print(f"    • all_tenders.csv  ({len(all_tenders)} rows combined)")

    print(f"\n  Dashboard: python3 dashboard.py → http://localhost:5000\n")


if __name__ == "__main__":
    asyncio.run(main())
