"""
Rich + Questionary terminal interface.
Provides an interactive menu to select portals, configure filters,
show live progress, and display results summary.
"""
from __future__ import annotations
import asyncio
from datetime import datetime
from pathlib import Path

import questionary
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress,
    SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich import box

from portals.configs import PORTALS, PortalConfig

console = Console()

BANNER = """
[bold cyan]
  ████████╗███████╗███╗   ██╗██████╗ ███████╗██████╗
  ╚══██╔══╝██╔════╝████╗  ██║██╔══██╗██╔════╝██╔══██╗
     ██║   █████╗  ██╔██╗ ██║██║  ██║█████╗  ██████╔╝
     ██║   ██╔══╝  ██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗
     ██║   ███████╗██║ ╚████║██████╔╝███████╗██║  ██║
     ╚═╝   ╚══════╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝
                   [white]Government Tender Scraping Agent[/white]
[/bold cyan]
"""


# ─── Portal selection ─────────────────────────────────────────────────────────

CATEGORY_LABELS = {
    "Central": "🏛️  Central Government",
    "PSU":     "🏭  Public Sector Undertakings",
    "State":   "🗺️  State Portals",
    "Info":    "ℹ️   Ministry Info Portals",
}

QUICK_PRESETS = {
    "All Central Govt":    [pid for pid, p in PORTALS.items() if p.category == "Central"],
    "All State Portals":   [pid for pid, p in PORTALS.items() if p.category == "State"],
    "All PSUs":            [pid for pid, p in PORTALS.items() if p.category == "PSU"],
    "GePNIC only":         [pid for pid, p in PORTALS.items() if p.platform == "gepnic"],
    "Everything":          list(PORTALS.keys()),
    "Custom selection...": [],
}


async def select_portals() -> list[str]:
    """Interactive multi-step portal selection."""
    console.print(BANNER)
    console.print(Rule("[bold yellow]Step 1 — Select Portals[/bold yellow]"))

    # Quick preset or custom
    preset = await questionary.select(
        "How would you like to select portals?",
        choices=list(QUICK_PRESETS.keys()),
        style=questionary.Style([
            ("selected", "fg:cyan bold"),
            ("pointer",  "fg:cyan bold"),
        ]),
    ).ask_async()

    if preset != "Custom selection...":
        chosen = QUICK_PRESETS[preset]
        console.print(f"\n[green]✓ Selected preset:[/green] {preset} ({len(chosen)} portals)\n")
        return chosen

    # Custom: group by category
    all_choices = []
    for cat, label in CATEGORY_LABELS.items():
        all_choices.append(questionary.Separator(f"\n  {label}"))
        for pid, cfg in PORTALS.items():
            if cfg.category == cat:
                status = "[dim](API)[/dim]" if cfg.platform == "gem_api" else ""
                all_choices.append(
                    questionary.Choice(
                        title=f"  {cfg.emoji}  {cfg.display_name} {status}",
                        value=pid,
                    )
                )

    chosen = await questionary.checkbox(
        "Select portals to scrape (Space to select, Enter to confirm):",
        choices=all_choices,
    ).ask_async()

    if not chosen:
        console.print("[red]No portals selected. Exiting.[/red]")
        return []

    console.print(f"\n[green]✓ Selected {len(chosen)} portal(s)[/green]\n")
    return chosen


# ─── Filter configuration ─────────────────────────────────────────────────────

async def configure_filters() -> dict:
    console.print(Rule("[bold yellow]Step 2 — Configure Filters[/bold yellow]"))

    max_pages = await questionary.text(
        "Max pages per portal? (press Enter for ALL pages):",
        default="",
    ).ask_async()

    org = await questionary.text(
        "Filter by organisation name? (press Enter to skip):",
        default="",
    ).ask_async()

    fetch_details = await questionary.confirm(
        "Fetch detail pages for each tender? (slower but richer data)",
        default=False,
    ).ask_async()

    console.print(Rule("[bold yellow]Step 3 — Export Formats[/bold yellow]"))
    formats = await questionary.checkbox(
        "Select export formats:",
        choices=[
            questionary.Choice("CSV  (.csv) — Excel/Sheets compatible", value="csv", checked=True),
            questionary.Choice("JSON (.json) — Full structured data",    value="json", checked=True),
            questionary.Choice("SQLite (.db) — Queryable database",      value="sqlite"),
        ],
    ).ask_async()

    return {
        "max_pages":      int(max_pages) if max_pages.strip().isdigit() else None,
        "org_filter":     org.strip() or None,
        "fetch_details":  fetch_details,
        "export_formats": formats or ["csv", "json"],
    }


# ─── Confirmation ─────────────────────────────────────────────────────────────

def show_run_plan(portals: list[str], filters: dict):
    table = Table(
        title="[bold]Run Plan[/bold]",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
    )
    table.add_column("Setting",      style="bold white", width=22)
    table.add_column("Value",        style="cyan")

    table.add_row("Portals",         ", ".join(portals))
    table.add_row("Max pages",       str(filters["max_pages"]) if filters["max_pages"] else "ALL")
    table.add_row("Org filter",      filters["org_filter"] or "None")
    table.add_row("Fetch details",   "Yes" if filters["fetch_details"] else "No")
    table.add_row("Export formats",  ", ".join(filters["export_formats"]))
    table.add_row("Output dir",      "output/")

    console.print(table)
    console.print()


async def confirm_start(portals: list[str], filters: dict) -> bool:
    show_run_plan(portals, filters)
    return await questionary.confirm("Start scraping now?", default=True).ask_async()


# ─── Live progress display ────────────────────────────────────────────────────

def build_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}[/bold cyan]"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TextColumn("•"),
        TextColumn("[yellow]{task.fields[tenders]}[/yellow] tenders"),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    )


# ─── Results summary ──────────────────────────────────────────────────────────

def show_results_summary(all_results: dict, new_counts: dict, output_dir: Path):
    """Print a per-portal summary table and new tender highlights."""
    console.print()
    console.print(Rule("[bold green]Scrape Complete[/bold green]"))

    # Per-portal table
    table = Table(
        title="[bold]Results by Portal[/bold]",
        box=box.ROUNDED,
        border_style="green",
    )
    table.add_column("Portal",   style="bold white", min_width=35)
    table.add_column("Total",    style="cyan",   justify="right")
    table.add_column("New",      style="green",  justify="right")
    table.add_column("Pages",    style="yellow", justify="right")
    table.add_column("Status",   style="white")

    total_tenders = 0
    total_new     = 0

    for portal_id, result in all_results.items():
        cfg     = PORTALS[portal_id]
        new_cnt = new_counts.get(portal_id, 0)
        total_tenders += len(result.tenders)
        total_new     += new_cnt

        if result.skipped:
            status = f"[yellow]⚠ {result.skip_reason[:40]}[/yellow]"
        elif result.errors:
            status = f"[red]✗ {result.errors[-1][:40]}[/red]"
        else:
            status = "[green]✓ OK[/green]"

        table.add_row(
            f"{cfg.emoji}  {cfg.display_name}",
            str(len(result.tenders)),
            f"[bold green]+{new_cnt}[/bold green]" if new_cnt else "0",
            str(result.pages),
            status,
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold cyan]{total_tenders}[/bold cyan]",
        f"[bold green]+{total_new}[/bold green]",
        "",
        "",
    )
    console.print(table)

    # New tender highlights
    if total_new > 0:
        console.print()
        console.print(Panel(
            f"[bold green]{total_new} NEW tenders found[/bold green] since last run",
            border_style="green",
        ))

    # Output files
    console.print()
    console.print("[bold white]Output files:[/bold white]")
    for f in sorted(output_dir.glob("*.*")):
        size_kb = f.stat().st_size // 1024
        console.print(f"  [cyan]{f}[/cyan]  [dim]({size_kb} KB)[/dim]")

    console.print()
    console.print(Rule("[dim]Done[/dim]"))


def show_new_tenders_detail(new_tenders: list[dict]):
    """Print a preview table of new tenders."""
    if not new_tenders:
        return

    table = Table(
        title=f"[bold green]New Tenders ({len(new_tenders)})[/bold green]",
        box=box.SIMPLE_HEAD,
        show_lines=True,
    )
    table.add_column("Portal",      style="cyan",  max_width=12)
    table.add_column("Tender ID",   style="yellow", max_width=22)
    table.add_column("Title",       style="white",  max_width=55)
    table.add_column("Org",         style="dim",    max_width=30)
    table.add_column("Closes",      style="red",    max_width=20)

    for t in new_tenders[:50]:   # cap at 50 for readability
        table.add_row(
            PORTALS.get(t.get("portal_id", ""), PortalConfig(
                portal_id="?", display_name="?", base_url="", platform="", category=""
            )).emoji + " " + t.get("portal_id", "?"),
            t.get("tender_id", "N/A")[:20],
            t.get("title", "N/A")[:53],
            t.get("organisation", "")[:28],
            t.get("closing_date", "N/A"),
        )

    if len(new_tenders) > 50:
        table.add_row("...", f"(+{len(new_tenders)-50} more)", "", "", "")

    console.print(table)
