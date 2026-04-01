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
from core.storage import OUTPUT_DIR
from core.orchestrator import ScrapeTask

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)
CORS(app)

# ── Active scrape tasks ────────────────────────────────────────────────────────
_tasks: dict[str, ScrapeTask] = {}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portals")
def get_portals():
    portals = []
    for pid, cfg in PORTALS.items():
        portals.append({
            "id":          pid,
            "name":        cfg.display_name,
            "category":    cfg.category,
            "emoji":       cfg.emoji,
            "platform":    cfg.platform,
            "has_archive": bool(cfg.archive_url),
            "has_awards":  bool(cfg.awards_url),
        })
    return jsonify(portals)


@app.route("/api/scrape", methods=["POST"])
def start_scrape():
    body       = request.get_json(force=True) or {}
    portal_ids = body.get("portals", [])
    filters    = body.get("filters", {})

    invalid = [p for p in portal_ids if p not in PORTALS]
    if invalid:
        return jsonify({"error": f"Unknown portals: {invalid}"}), 400
    if not portal_ids:
        return jsonify({"error": "No portals selected"}), 400

    task_id = str(uuid.uuid4())[:8]
    task    = ScrapeTask(task_id=task_id, portal_ids=portal_ids, filters=filters)
    _tasks[task_id] = task

    # Run in a dedicated background thread with its own asyncio event loop.
    # Using a thread (not asyncio.create_task) keeps Flask's sync model intact
    # and avoids cross-loop asyncio.Queue issues on Python 3.9.
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(task.run())
        except Exception as e:
            task.emit({"type": "error", "message": str(e)})
            task.emit({"type": "done",  "total": 0})
            task._done = True
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"task_id": task_id})


@app.route("/api/stream/<task_id>")
def stream_events(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "Unknown task"}), 404

    def _generate() -> Iterator[str]:
        while True:
            # next_event() blocks up to 2 s, returns None on timeout
            ev = task.next_event(timeout=2.0)

            if ev is None:
                # Still waiting — send a heartbeat comment to keep connection alive
                if task._done:
                    break
                yield ": heartbeat\n\n"
                continue

            yield f"data: {json.dumps(ev)}\n\n"

            if ev.get("type") == "done":
                break

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


@app.route("/api/tenders")
def get_tenders():
    portal   = request.args.get("portal", "")
    search   = request.args.get("search", "").lower()
    status   = request.args.get("status", "")
    sort_col = request.args.get("sort", "scraped_at")
    sort_dir = request.args.get("dir", "desc")
    page     = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 50)), 200)

    rows = _load_tenders(portal_filter=portal)

    if search:
        rows = [r for r in rows if
                search in r.get("title", "").lower() or
                search in r.get("organisation", "").lower() or
                search in r.get("tender_id", "").lower()]
    if status:
        rows = [r for r in rows if r.get("status", "").lower() == status.lower()]

    rows.sort(key=lambda r: r.get(sort_col, "") or "", reverse=(sort_dir == "desc"))

    total = len(rows)
    start = (page - 1) * per_page
    return jsonify({
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    (total + per_page - 1) // per_page,
        "tenders":  rows[start: start + per_page],
    })


@app.route("/api/export")
def export_csv():
    portal = request.args.get("portal", "")
    path   = (OUTPUT_DIR / f"{portal}_tenders.csv") if portal in PORTALS else (OUTPUT_DIR / "all_tenders.csv")
    if not path.exists():
        return jsonify({"error": "No data yet — run a scrape first"}), 404
    return send_file(str(path.resolve()), mimetype="text/csv",
                     as_attachment=True, download_name=path.name)


@app.route("/api/stats")
def get_stats():
    rows = _load_tenders()
    by_portal = {}
    for r in rows:
        pid = r.get("portal_id", "unknown")
        by_portal[pid] = by_portal.get(pid, 0) + 1
    awarded = sum(1 for r in rows if r.get("award_winner") or r.get("award_date"))
    return jsonify({"total_tenders": len(rows), "awarded": awarded, "by_portal": by_portal})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_tenders(portal_filter: str = "") -> list[dict]:
    import csv
    if portal_filter and portal_filter in PORTALS:
        paths = [OUTPUT_DIR / f"{portal_filter}_tenders.csv"]
    else:
        combined = OUTPUT_DIR / "all_tenders.csv"
        paths = [combined] if combined.exists() else list(OUTPUT_DIR.glob("*_tenders.csv"))

    rows = []
    for p in paths:
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                rows.extend(list(csv.DictReader(f)))
        except Exception:
            pass
    return rows


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
