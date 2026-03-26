"""
Flask web dashboard for Tender Agent.
Run via: python dashboard.py
API:
  GET  /                      — dashboard HTML
  GET  /api/portals           — portal list
  POST /api/scrape            — start scraping job → {task_id}
  GET  /api/stream/<task_id>  — SSE progress stream
  GET  /api/tenders           — query/filter stored tenders
  GET  /api/export            — download tenders.csv
  GET  /api/stats             — summary stats
"""
from __future__ import annotations
import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Iterator

from flask import Flask, Response, jsonify, render_template, request, send_file
from flask_cors import CORS

from portals.configs import PORTALS
from core.storage import OUTPUT_DIR, FULL_FIELDS
from core.orchestrator import ScrapeTask

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)
CORS(app)

# ── Active scrape tasks ────────────────────────────────────────────────────────
_tasks: dict[str, ScrapeTask] = {}
_loops: dict[str, asyncio.AbstractEventLoop] = {}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/portals")
def get_portals():
    portals = []
    for pid, cfg in PORTALS.items():
        portals.append({
            "id":       pid,
            "name":     cfg.display_name,
            "category": cfg.category,
            "emoji":    cfg.emoji,
            "platform": cfg.platform,
            "has_archive": bool(cfg.archive_url),
            "has_awards":  bool(cfg.awards_url),
        })
    return jsonify(portals)


@app.post("/api/scrape")
def start_scrape():
    body       = request.get_json(force=True) or {}
    portal_ids = body.get("portals", [])
    filters    = body.get("filters", {})

    # Validate
    invalid = [p for p in portal_ids if p not in PORTALS]
    if invalid:
        return jsonify({"error": f"Unknown portals: {invalid}"}), 400
    if not portal_ids:
        return jsonify({"error": "No portals selected"}), 400

    task_id = str(uuid.uuid4())[:8]
    task    = ScrapeTask(task_id=task_id, portal_ids=portal_ids, filters=filters)
    _tasks[task_id] = task

    # Run in a dedicated background thread with its own event loop
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loops[task_id] = loop
        try:
            loop.run_until_complete(task.run())
        finally:
            loop.close()
            _loops.pop(task_id, None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({"task_id": task_id})


@app.get("/api/stream/<task_id>")
def stream_events(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "Unknown task"}), 404

    def _generate() -> Iterator[str]:
        loop = None
        # Wait for the loop to be created
        import time
        for _ in range(20):
            loop = _loops.get(task_id)
            if loop:
                break
            time.sleep(0.1)

        if not loop:
            yield "data: {\"type\": \"error\", \"message\": \"Loop not found\"}\n\n"
            return

        # Poll the async queue from the sync generator
        while True:
            future = asyncio.run_coroutine_threadsafe(
                _poll_event(task), loop
            )
            try:
                ev = future.result(timeout=2.0)
            except Exception:
                ev = None

            if ev is not None:
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("type") == "done":
                    break
            elif task._done:
                break

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _poll_event(task: ScrapeTask):
    try:
        return await asyncio.wait_for(task._events.get(), timeout=1.5)
    except asyncio.TimeoutError:
        return None


@app.get("/api/tenders")
def get_tenders():
    """Return tenders from the combined CSV (or per-portal)."""
    portal    = request.args.get("portal", "")
    search    = request.args.get("search", "").lower()
    status    = request.args.get("status", "")
    sort_col  = request.args.get("sort", "scraped_at")
    sort_dir  = request.args.get("dir", "desc")
    page      = int(request.args.get("page", 1))
    per_page  = min(int(request.args.get("per_page", 50)), 200)

    rows = _load_tenders(portal_filter=portal)

    # Filter
    if search:
        rows = [r for r in rows if search in r.get("title", "").lower()
                or search in r.get("organisation", "").lower()
                or search in r.get("tender_id", "").lower()]
    if status:
        rows = [r for r in rows if r.get("status", "").lower() == status.lower()]

    # Sort
    reverse = sort_dir == "desc"
    rows.sort(key=lambda r: r.get(sort_col, "") or "", reverse=reverse)

    total = len(rows)
    start = (page - 1) * per_page
    paged = rows[start: start + per_page]

    return jsonify({
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    (total + per_page - 1) // per_page,
        "tenders":  paged,
    })


@app.get("/api/export")
def export_csv():
    """Download all_tenders.csv (or specific portal CSV)."""
    portal = request.args.get("portal", "")
    if portal and portal in PORTALS:
        path = OUTPUT_DIR / f"{portal}_tenders.csv"
    else:
        path = OUTPUT_DIR / "all_tenders.csv"

    if not path.exists():
        return jsonify({"error": "No data yet — run a scrape first"}), 404

    return send_file(
        str(path.resolve()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=path.name,
    )


@app.get("/api/stats")
def get_stats():
    rows = _load_tenders()
    portals_seen = {}
    for r in rows:
        pid = r.get("portal_id", "unknown")
        portals_seen[pid] = portals_seen.get(pid, 0) + 1

    awarded = sum(1 for r in rows if r.get("award_winner") or r.get("award_date"))

    return jsonify({
        "total_tenders": len(rows),
        "awarded":        awarded,
        "by_portal":      portals_seen,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_tenders(portal_filter: str = "") -> list[dict]:
    """Load tenders from CSV files in OUTPUT_DIR."""
    import csv

    if portal_filter and portal_filter in PORTALS:
        paths = [OUTPUT_DIR / f"{portal_filter}_tenders.csv"]
    else:
        combined = OUTPUT_DIR / "all_tenders.csv"
        if combined.exists():
            paths = [combined]
        else:
            paths = list(OUTPUT_DIR.glob("*_tenders.csv"))

    rows = []
    for p in paths:
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows.extend(list(reader))
        except Exception:
            pass
    return rows
