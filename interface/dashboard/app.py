"""
Flask web dashboard — fully updated.
- Reads from SQLite (accumulated all-time data)
- Statewise, pricewise, category, date range filters
- /api/stats returns rich analytics
"""
from __future__ import annotations
import asyncio, csv, io, json, logging, os, threading, uuid
from pathlib import Path
from typing import Iterator

from flask import Flask, Response, jsonify, render_template, request, send_file
from flask_cors import CORS

from portals.configs import PORTALS
from core.storage import (
    OUTPUT_DIR, load_all_from_sqlite, load_portal_from_sqlite,
    get_db_stats, save_combined_csv, FULL_FIELDS,
)
from core.orchestrator import ScrapeTask
from core.cleaner import TenderCleaner

log = logging.getLogger("dashboard.app")

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

_tasks:       dict[str, ScrapeTask] = {}
_clean_cache: dict[str, list[dict]] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_tenders(portal_filter: str = "") -> list[dict]:
    """Load from SQLite master DB (accumulated all-time data)."""
    if portal_filter and portal_filter in PORTALS:
        return load_portal_from_sqlite(portal_filter)
    return load_all_from_sqlite()


def _apply_filters(rows: list[dict], args) -> list[dict]:
    """Apply all query filters."""
    search     = args.get("search",    "").lower()
    status     = args.get("status",    "")
    state      = args.get("state",     "")
    category   = args.get("category",  "")
    tender_type = args.get("type",     "")
    date_from  = args.get("date_from", "")
    date_to    = args.get("date_to",   "")
    price_min  = args.get("price_min", "")
    price_max  = args.get("price_max", "")

    if search:
        rows = [r for r in rows if
                search in (r.get("title","") or "").lower() or
                search in (r.get("organisation","") or "").lower() or
                search in (r.get("tender_id","") or "").lower() or
                search in (r.get("state","") or "").lower()]

    if status:
        rows = [r for r in rows if (r.get("status","") or "").lower() == status.lower()]

    if state:
        rows = [r for r in rows if state.lower() in (r.get("state","") or "").lower()]

    if category:
        rows = [r for r in rows if category.lower() in (r.get("tender_category","") or "").lower()
                or category.lower() in (r.get("product_category","") or "").lower()]

    if tender_type:
        rows = [r for r in rows if tender_type.lower() in (r.get("tender_type","") or "").lower()]

    if date_from:
        rows = [r for r in rows if (r.get("closing_date","") or "") >= date_from]

    if date_to:
        rows = [r for r in rows if (r.get("closing_date","") or "") <= date_to]

    if price_min:
        try:
            mn = float(price_min)
            rows = [r for r in rows if _to_num(r.get("tender_value_inr","")) >= mn]
        except ValueError:
            pass

    if price_max:
        try:
            mx = float(price_max)
            rows = [r for r in rows if 0 < _to_num(r.get("tender_value_inr","")) <= mx]
        except ValueError:
            pass

    return rows


def _to_num(val: str) -> float:
    try:
        s = str(val).replace(",","").replace("₹","").strip()
        if not s:
            return 0
        m_val = float(re.sub(r"[^\d.]", "", s.split()[0]))
        s_lower = s.lower()
        if   "cr" in s_lower: m_val *= 1e7
        elif "lakh" in s_lower or " l" in s_lower: m_val *= 1e5
        return m_val
    except Exception:
        return 0


import re


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/portals")
def get_portals():
    portals = []
    for pid, cfg in PORTALS.items():
        portals.append({
            "id":          pid,
            "name":        cfg.display_name,
            "category":    cfg.category,
            "emoji":       cfg.emoji,
            "platform":    cfg.platform,
            "state":       getattr(cfg, "state", ""),
            "has_archive": bool(getattr(cfg, "archive_url", "")),
            "has_awards":  bool(getattr(cfg, "awards_url", "")),
        })
    return jsonify(portals)


@app.post("/api/scrape")
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
    _clean_cache.clear()

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


@app.get("/api/stream/<task_id>")
def stream_events(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "Unknown task"}), 404

    def _generate() -> Iterator[str]:
        while True:
            ev = task.next_event(timeout=2.0)
            if ev is None:
                if task._done:
                    break
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") == "done":
                break

    return Response(_generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


@app.get("/api/tenders")
def get_tenders():
    portal   = request.args.get("portal", "")
    sort_col = request.args.get("sort", "scraped_at")
    sort_dir = request.args.get("dir",  "desc")
    page     = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 50)), 200)
    use_clean = request.args.get("cleaned", "false").lower() == "true"

    if use_clean and "cleaned" in _clean_cache:
        rows = list(_clean_cache["cleaned"])
    else:
        rows = _load_tenders(portal_filter=portal)

    rows = _apply_filters(rows, request.args)
    rows.sort(
        key=lambda r: r.get(sort_col, "") or "",
        reverse=(sort_dir == "desc")
    )

    total = len(rows)
    start = (page - 1) * per_page
    return jsonify({
        "total":   total,
        "page":    page,
        "per_page": per_page,
        "pages":   (total + per_page - 1) // per_page,
        "tenders": rows[start: start + per_page],
    })


@app.get("/api/stats")
def get_stats():
    """Rich stats from SQLite — statewise, pricewise, portal breakdown."""
    stats = get_db_stats()

    # Also get filter options for dropdowns
    rows   = load_all_from_sqlite()
    states = sorted({r.get("state","") for r in rows if r.get("state","")})
    cats   = sorted({r.get("tender_category","") for r in rows if r.get("tender_category","")})
    types  = sorted({r.get("tender_type","") for r in rows if r.get("tender_type","")})

    return jsonify({
        **stats,
        "active":   stats.get("by_status",{}).get("Active",  0),
        "archived": stats.get("by_status",{}).get("Archive", 0),
        "filter_options": {
            "states":     states[:50],
            "categories": cats[:30],
            "types":      types[:20],
        },
        "has_cleaned": "cleaned" in _clean_cache,
    })


@app.get("/api/export")
def export_csv():
    portal    = request.args.get("portal", "")
    use_clean = request.args.get("cleaned","false").lower() == "true"
    scope     = request.args.get("scope", "all")

    if use_clean and "cleaned" in _clean_cache:
        rows = _clean_cache["cleaned"]
        path = OUTPUT_DIR / "all_tenders_cleaned.csv"
        _write_csv(rows, path)
        return send_file(str(path.resolve()), mimetype="text/csv",
                         as_attachment=True, download_name="all_tenders_cleaned.csv")

    # Export from SQLite (complete accumulated data)
    if portal and portal in PORTALS:
        rows = load_portal_from_sqlite(portal)
        fname = f"{portal}_tenders.csv"
    else:
        rows  = load_all_from_sqlite()
        # Filter by scope
        if scope == "archive":
            rows = [r for r in rows if (r.get("status","") or "").lower() in ("archive","closed","expired")]
        elif scope == "awards":
            rows = [r for r in rows if r.get("award_winner") or r.get("award_date")]

        fname = f"tenders_{scope}.csv" if scope != "all" else "all_tenders.csv"

    if not rows:
        return jsonify({"error": "No data — run a scrape first"}), 404

    path = OUTPUT_DIR / fname
    _write_csv(rows, path)
    return send_file(str(path.resolve()), mimetype="text/csv",
                     as_attachment=True, download_name=fname)


def _write_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FULL_FIELDS})


@app.get("/api/captcha/status")
def captcha_status():
    return jsonify({
        "openai":      bool(os.getenv("OPENAI_API_KEY")),
        "2captcha":    bool(os.getenv("TWOCAPTCHA_KEY")),
        "anticaptcha": bool(os.getenv("ANTICAPTCHA_KEY")),
        "capabilities": {
            "image_text": bool(os.getenv("OPENAI_API_KEY")),
            "math":       True,
            "slider":     True,
            "recaptcha":  bool(os.getenv("TWOCAPTCHA_KEY") or os.getenv("ANTICAPTCHA_KEY")),
            "hcaptcha":   bool(os.getenv("TWOCAPTCHA_KEY")),
            "audio":      bool(os.getenv("OPENAI_API_KEY")),
        },
    })


@app.post("/api/clean")
def clean_data():
    body              = request.get_json(force=True) or {}
    portal            = body.get("portal", "")
    fuzz_thresh       = float(body.get("fuzzy_dedup_threshold", 0.92))
    remove_incomplete = bool(body.get("remove_incomplete", False))
    min_completeness  = float(body.get("min_completeness", 0.20))
    save_to_disk      = bool(body.get("save", True))

    raw = _load_tenders(portal_filter=portal)
    if not raw:
        return jsonify({"error": "No data — run a scrape first"}), 404

    cleaner = TenderCleaner(
        fuzzy_dedup_threshold=fuzz_thresh,
        remove_incomplete=remove_incomplete,
        min_completeness=min_completeness,
    )
    cleaned = cleaner.clean_batch(raw)
    report  = cleaner.report(raw, cleaned)
    _clean_cache["cleaned"] = cleaned

    if save_to_disk:
        _write_csv(cleaned, OUTPUT_DIR / "all_tenders_cleaned.csv")

    preview_fields = ["portal_id","tender_id","title","organisation","state",
                      "published_date","closing_date","tender_value_inr","status","_completeness"]
    preview = [{f: r.get(f,"") for f in preview_fields} for r in cleaned[:10]]

    return jsonify({"report": report, "preview": preview, "saved": save_to_disk})


@app.get("/api/clean/download")
def download_cleaned():
    if "cleaned" in _clean_cache:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=FULL_FIELDS+["_completeness"], extrasaction="ignore")
        writer.writeheader()
        for row in _clean_cache["cleaned"]:
            writer.writerow({f: row.get(f,"") for f in FULL_FIELDS+["_completeness"]})
        output.seek(0)
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":"attachment; filename=all_tenders_cleaned.csv"})

    path = OUTPUT_DIR / "all_tenders_cleaned.csv"
    if path.exists():
        return send_file(str(path.resolve()), mimetype="text/csv",
                         as_attachment=True, download_name="all_tenders_cleaned.csv")
    return jsonify({"error": "No cleaned data — run /api/clean first"}), 404
