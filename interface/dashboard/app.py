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
  GET  /api/analytics         — full analytics (categories, daily, coverage)
"""
from __future__ import annotations
import asyncio
import json
import os
import threading
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Iterator
from collections import Counter

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
            "has_archive": bool(cfg.archive_url),
            "has_awards":  bool(cfg.awards_url),
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


@app.get("/api/stream/<task_id>")
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


@app.get("/api/tenders")
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


@app.get("/api/export")
def export_csv():
    portal = request.args.get("portal", "")
    path   = (OUTPUT_DIR / f"{portal}_tenders.csv") if portal in PORTALS else (OUTPUT_DIR / "all_tenders.csv")
    if not path.exists():
        return jsonify({"error": "No data yet — run a scrape first"}), 404
    return send_file(str(path.resolve()), mimetype="text/csv",
                     as_attachment=True, download_name=path.name)


@app.get("/api/stats")
def get_stats():
    """Summary stats — uses SQL aggregation when possible for speed."""
    import sqlite3
    db_path = OUTPUT_DIR / "tenders.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            total = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
            # Count by status groups — mutually exclusive
            active = conn.execute(
                "SELECT COUNT(*) FROM tenders WHERE LOWER(COALESCE(status,'')) IN ('active', 'published', '')"
            ).fetchone()[0]
            awarded = conn.execute(
                "SELECT COUNT(*) FROM tenders WHERE LOWER(status) = 'awarded'"
            ).fetchone()[0]
            archive = conn.execute(
                "SELECT COUNT(*) FROM tenders WHERE LOWER(status) IN ('archive', 'closed')"
            ).fetchone()[0]
            with_details = conn.execute(
                "SELECT COUNT(*) FROM tenders WHERE detail_scraped IS NOT NULL"
                " AND detail_scraped != '' AND detail_scraped != '0' AND detail_scraped != 'False'"
            ).fetchone()[0]
            by_portal = {}
            for row in conn.execute("SELECT portal_id, COUNT(*) FROM tenders GROUP BY portal_id"):
                by_portal[row[0] or "unknown"] = row[1]
            conn.close()
            return jsonify({
                "total_tenders": total,
                "awarded": awarded,
                "with_details": with_details,
                "active_count": active,
                "archive_count": archive,
                "by_portal": by_portal,
                "portal_count": len(by_portal),
            })
        except Exception:
            pass

    # Fallback to in-memory
    rows = _load_tenders()
    by_portal = {}
    for r in rows:
        pid = r.get("portal_id", "unknown")
        by_portal[pid] = by_portal.get(pid, 0) + 1
    active = sum(1 for r in rows if r.get("status", "").lower() in ("active", "published", ""))
    awarded = sum(1 for r in rows if r.get("status", "").lower() == "awarded")
    archive = sum(1 for r in rows if r.get("status", "").lower() in ("archive", "closed"))
    with_details = sum(1 for r in rows if r.get("detail_scraped") and r.get("detail_scraped") not in ("", "0", "False"))
    return jsonify({
        "total_tenders": len(rows),
        "awarded": awarded,
        "with_details": with_details,
        "active_count": active,
        "archive_count": archive,
        "by_portal": by_portal,
        "portal_count": len(by_portal),
    })


@app.get("/api/analytics")
def get_analytics():
    """Full analytics data for the dashboard charts and tables."""
    rows = _load_tenders()
    today = date.today().strftime("%Y-%m-%d")

    # Per-portal breakdown with details coverage
    portal_stats = {}
    for r in rows:
        pid = r.get("portal_id", "unknown")
        if pid not in portal_stats:
            cfg = PORTALS.get(pid)
            portal_stats[pid] = {
                "portal_id": pid,
                "name": cfg.display_name if cfg else pid,
                "emoji": cfg.emoji if cfg else "",
                "category": cfg.category if cfg else "Other",
                "total": 0,
                "with_details": 0,
                "active": 0,
                "archive": 0,
                "awarded": 0,
            }
        s = portal_stats[pid]
        s["total"] += 1
        if r.get("detail_scraped") and r.get("detail_scraped") not in ("", "0", "False"):
            s["with_details"] += 1
        status = r.get("status", "").lower()
        if status in ("archive", "closed"):
            s["archive"] += 1
        elif status == "awarded":
            s["awarded"] += 1
        else:
            s["active"] += 1

    # Industry category breakdown
    categories = Counter()
    for r in rows:
        cat = r.get("industry_category", "").strip()
        if cat:
            categories[cat] += 1

    # Status breakdown
    status_counts = Counter()
    for r in rows:
        st = r.get("status", "Active").strip() or "Active"
        status_counts[st] += 1

    # Closing soon (next 7 days)
    closing_soon = []
    for r in rows:
        cd = r.get("closing_date", "")
        if cd and cd >= today:
            closing_soon.append(r)
    closing_soon.sort(key=lambda r: r.get("closing_date", ""))
    closing_soon = closing_soon[:20]  # Top 20

    # Expiring in 2 days (urgent)
    two_days = (date.today() + __import__('datetime').timedelta(days=2)).strftime("%Y-%m-%d")
    expiring_2d = []
    for r in rows:
        cd = r.get("closing_date", "")
        if cd and cd >= today and cd <= two_days:
            expiring_2d.append(r)
    expiring_2d.sort(key=lambda r: r.get("closing_date", ""))

    # Top organisations
    org_counts = Counter()
    for r in rows:
        org = r.get("organisation", "").strip()
        if org:
            org_counts[org] += 1
    top_orgs = org_counts.most_common(15)

    # Scrape date histogram (tenders per scrape date)
    date_counts = Counter()
    for r in rows:
        sd = r.get("scrape_date", "").strip() or r.get("scraped_at", "")[:10]
        if sd:
            date_counts[sd] += 1

    # Category by portal (for stacked chart)
    cat_by_portal = {}
    for r in rows:
        pid = r.get("portal_id", "unknown")
        cat = r.get("industry_category", "Other").strip() or "Other"
        if pid not in cat_by_portal:
            cat_by_portal[pid] = Counter()
        cat_by_portal[pid][cat] += 1

    return jsonify({
        "portal_stats": sorted(portal_stats.values(), key=lambda x: x["total"], reverse=True),
        "categories": [{"name": k, "count": v} for k, v in categories.most_common()],
        "status_breakdown": dict(status_counts),
        "closing_soon": closing_soon[:20],
        "expiring_2days": expiring_2d[:50],
        "top_organisations": [{"name": n, "count": c} for n, c in top_orgs],
        "scrape_dates": [{"date": k, "count": v} for k, v in sorted(date_counts.items())],
        "total": len(rows),
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_tenders(portal_filter: str = "") -> list[dict]:
    """Load tenders from SQLite (real-time merged data) with CSV fallback."""
    import sqlite3
    db_path = OUTPUT_DIR / "tenders.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            if portal_filter and portal_filter in PORTALS:
                cursor = conn.execute("SELECT * FROM tenders WHERE portal_id = ?", (portal_filter,))
            else:
                cursor = conn.execute("SELECT * FROM tenders")
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            if rows:
                return rows
        except Exception:
            pass

    # Fallback to CSV
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


@app.get("/api/schema")
def get_schema():
    """Return the full tender schema with field descriptions."""
    from core.storage import FULL_FIELDS
    field_descriptions = {
        "portal_id": "Source portal identifier",
        "portal_name": "Full portal display name",
        "tender_id": "Unique tender identifier",
        "ref_number": "Reference/NIT number",
        "title": "Tender title/subject",
        "organisation": "Issuing organisation",
        "published_date": "Date tender was published",
        "closing_date": "Bid submission deadline",
        "opening_date": "Bid opening date",
        "status": "Active / Archive / Awarded",
        "detail_url": "Link to full tender page",
        "scraped_at": "Timestamp of scrape",
        "page_num": "Listing page number",
        "detail_scraped": "Whether detail page was fetched",
        "tender_value_inr": "Estimated contract value (INR)",
        "tender_fee_inr": "Tender document fee (INR)",
        "emd_inr": "Earnest Money Deposit (INR)",
        "emd_fee_type": "EMD payment type (cash/BG/online)",
        "tender_type": "Open/Limited/Single/Two-stage",
        "tender_category": "Works/Goods/Services/Consultancy",
        "product_category": "Product/service classification",
        "form_of_contract": "Contract form (item rate, lump sum, etc)",
        "payment_mode": "Payment method",
        "bid_submission_start": "Bid submission window start",
        "bid_submission_end": "Bid submission window end",
        "doc_download_start": "Document download start date",
        "doc_download_end": "Document download end date",
        "clarification_start": "Clarification period start",
        "clarification_end": "Clarification period end",
        "pre_bid_meeting": "Pre-bid meeting date",
        "bid_validity": "Bid validity period",
        "work_description": "Scope of work / description",
        "two_stage_bid": "Whether two-stage bidding allowed",
        "nda_allowed": "Whether NDA is allowed",
        "location": "Work/delivery location",
        "pincode": "PIN code",
        "contact": "Contact person/authority",
        "fee_payable_to": "Tender fee payable to",
        "emd_payable_to": "EMD payable to",
        "documents": "Attached document links",
        "gem_category": "GeM product category",
        "gem_quantity": "GeM quantity",
        "gem_consignee": "GeM consignee",
        "award_winner": "Winning bidder name",
        "award_date": "Award date",
        "award_amount": "Awarded contract value",
        "aoc_no": "Award of Contract reference",
        "industry_category": "AI-classified industry (25 categories)",
        "sub_category": "AI-classified sub-category",
        "department": "Department name",
        "scrape_date": "Date partition key (YYYY-MM-DD)",
        "source_portal_type": "government / aggregator",
        "kppp_tab": "KPPP tab (goods/works/services)",
        "scope_scraped": "Scrape scope (active/archive/awards)",
    }
    schema = []
    for f in FULL_FIELDS:
        schema.append({"field": f, "description": field_descriptions.get(f, "")})
    return jsonify({"fields": schema, "total_fields": len(FULL_FIELDS)})
