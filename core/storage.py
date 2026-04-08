"""
Storage layer — CSV, JSON, SQLite exporters + snapshot diff store.
All formats share the same FULL_FIELDS schema so CSV always has every column.
"""
from __future__ import annotations
import csv
import json
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path

OUTPUT_DIR   = Path("output")
SNAPSHOT_DIR = Path("output/snapshots")
DAILY_DIR    = Path("output/daily")
LOG_DIR      = Path("logs")

# ── Complete field list — every tender row has ALL these columns ───────────────
FULL_FIELDS = [
    # ── Identity ──────────────────────────────────────────
    "portal_id",
    "portal_name",
    "tender_id",
    "ref_number",
    # ── Listing fields ────────────────────────────────────
    "title",
    "organisation",
    "published_date",
    "closing_date",
    "opening_date",
    "status",
    "detail_url",
    "scraped_at",
    "page_num",
    # ── Detail fields (filled when fetch_details=True) ────
    "detail_scraped",
    "tender_value_inr",
    "tender_fee_inr",
    "emd_inr",
    "emd_fee_type",
    "tender_type",
    "tender_category",
    "product_category",
    "form_of_contract",
    "payment_mode",
    "bid_submission_start",
    "bid_submission_end",
    "doc_download_start",
    "doc_download_end",
    "clarification_start",
    "clarification_end",
    "pre_bid_meeting",
    "bid_validity",
    "work_description",
    "two_stage_bid",
    "nda_allowed",
    "location",
    "pincode",
    "contact",
    "fee_payable_to",
    "emd_payable_to",
    "documents",
    # ── GeM-specific extras ───────────────────────────────
    "gem_category",
    "gem_quantity",
    "gem_consignee",
    # ── Award / Result of Tender (filled when tender is awarded) ──
    "award_winner",     # Winning vendor / contractor name
    "award_date",       # Award of Contract (AOC) date
    "award_amount",     # Contract value awarded
    "aoc_no",           # AOC reference number
    # ── AI-classified & metadata ──────────────────────────
    "industry_category",    # AI-classified category (IT Services, Construction, etc.)
    "sub_category",         # AI-classified sub-category
    "department",           # Department name (separate from organisation)
    "scrape_date",          # YYYY-MM-DD date partition key
    "source_portal_type",   # "government" or "aggregator"
    # ── KPPP-specific ─────────────────────────────────────────────
    "kppp_tab",         # KPPP tab: goods | works | services
    # ── Extended detail fields ────────────────────────────────────
    "scope_scraped",        # What scope was used: active/archive/awards
]

# Fields to include in the awards-only CSV (subset of FULL_FIELDS)
AWARD_FIELDS = [
    "portal_id", "portal_name", "tender_id", "ref_number",
    "title", "organisation", "published_date", "closing_date",
    "tender_value_inr", "detail_url", "scraped_at",
    "award_winner", "award_date", "award_amount", "aoc_no",
]


def _normalise(tender: dict) -> dict:
    """Ensure every field exists (empty string if missing)."""
    return {f: str(tender.get(f, "") or "").strip() for f in FULL_FIELDS}


def _dedup_key(tender: dict) -> str | None:
    """
    Generate a dedup key for a tender.
    Uses (portal_id, tender_id) as primary key.
    Falls back to (portal_id, ref_number) or (portal_id, title_hash).
    Returns None only if no usable identifier exists.
    """
    pid = str(tender.get("portal_id", "") or "").strip()
    tid = str(tender.get("tender_id", "") or "").strip()
    ref = str(tender.get("ref_number", "") or "").strip()
    title = str(tender.get("title", "") or "").strip()

    if pid and tid:
        return f"{pid}::{tid}"
    if pid and ref:
        return f"{pid}::ref::{ref}"
    if pid and title:
        # Use first 120 chars of title as fallback
        return f"{pid}::title::{title[:120]}"
    return None


# ─── CSV ──────────────────────────────────────────────────────────────────────

def save_csv(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR) -> Path:
    """Save tenders to CSV, merging with existing data and deduplicating."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{portal_id}_tenders.csv"

    # Load existing rows if file exists
    existing: dict[str, dict] = {}
    if path.exists() and path.stat().st_size > 0:
        try:
            with open(path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    key = _dedup_key(row)
                    if key:
                        existing[key] = row
        except Exception:
            pass

    # Merge new tenders (new values override empty old values)
    for t in tenders:
        norm = _normalise(t)
        key = _dedup_key(norm)
        if key and key in existing:
            norm = _merge_tender(existing[key], norm)
        if key:
            existing[key] = norm
        else:
            # No dedup key — just append
            existing[f"_nokey_{id(t)}"] = norm

    rows = list(existing.values())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def save_combined_csv(all_tenders: list[dict], output_dir: Path = OUTPUT_DIR) -> Path:
    """Single CSV with all portals combined, deduplicated and merged with existing."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "all_tenders.csv"

    # Load existing combined CSV
    existing: dict[str, dict] = {}
    if path.exists() and path.stat().st_size > 0:
        try:
            with open(path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    key = _dedup_key(row)
                    if key:
                        existing[key] = row
        except Exception:
            pass

    # Merge new tenders
    for t in all_tenders:
        norm = _normalise(t)
        key = _dedup_key(norm)
        if key and key in existing:
            norm = _merge_tender(existing[key], norm)
        if key:
            existing[key] = norm
        else:
            existing[f"_nokey_{id(t)}"] = norm

    rows = list(existing.values())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def save_awards_csv(tenders: list[dict], output_dir: Path = OUTPUT_DIR) -> Path:
    """
    Save only tenders that have award data (winner/AOC info) to a separate CSV.
    Returns path. Returns None if no awarded tenders found.
    """
    awarded = [t for t in tenders if t.get("award_winner") or t.get("award_date")]
    if not awarded:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "awarded_tenders.csv"

    def norm_award(t):
        return {f: str(t.get(f, "") or "").strip() for f in AWARD_FIELDS}

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=AWARD_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([norm_award(t) for t in awarded])
    return path


# ─── JSON ─────────────────────────────────────────────────────────────────────

def save_json(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{portal_id}_tenders.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tenders, f, indent=2, ensure_ascii=False, default=str)
    return path


# ─── Daily Snapshots ─────────────────────────────────────────────────────────

def save_daily_snapshot(tenders: list[dict], portal_id: str,
                        base_dir: Path = DAILY_DIR) -> Path:
    """
    Save a date-partitioned daily snapshot.
    Writes to output/daily/YYYY-MM-DD/{portal_id}_tenders.json.
    Each day's scrape is preserved separately for historical tracking.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    day_dir = base_dir / today_str
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{portal_id}_tenders.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tenders, f, indent=2, ensure_ascii=False, default=str)
    return path


# ─── SQLite ───────────────────────────────────────────────────────────────────

def _get_db(output_dir: Path = OUTPUT_DIR) -> sqlite3.Connection:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_dir / "tenders.db")
    cols = ", ".join(
        f"{f} TEXT" if f != "page_num" else "page_num INTEGER"
        for f in FULL_FIELDS
    )
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS tenders (
            {cols},
            PRIMARY KEY (portal_id, tender_id)
        )
    """)
    conn.commit()
    # Migrate: add any new columns that don't exist yet
    _migrate_columns(conn)
    return conn


def _migrate_columns(conn: sqlite3.Connection):
    """Add any FULL_FIELDS columns missing from the existing table."""
    cursor = conn.execute("PRAGMA table_info(tenders)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    for field in FULL_FIELDS:
        if field not in existing_cols:
            col_type = "INTEGER" if field == "page_num" else "TEXT"
            conn.execute(f"ALTER TABLE tenders ADD COLUMN {field} {col_type}")
    conn.commit()


def _merge_tender(existing: dict, new: dict) -> dict:
    """
    Merge two tender records for the same (portal_id, tender_id).
    For each field, keep the non-empty value. If both are non-empty,
    prefer the new value (fresher scrape).
    """
    merged = {}
    for f in FULL_FIELDS:
        new_val = str(new.get(f, "") or "").strip()
        old_val = str(existing.get(f, "") or "").strip()
        if new_val:
            merged[f] = new_val
        else:
            merged[f] = old_val
    return merged


def save_sqlite(tenders: list[dict], output_dir: Path = OUTPUT_DIR):
    if not tenders:
        return
    conn = _get_db(output_dir)
    conn.row_factory = sqlite3.Row

    for t in tenders:
        norm = _normalise(t)
        portal_id = norm.get("portal_id", "")
        tender_id = norm.get("tender_id", "")

        # Check for existing record to merge
        if portal_id and tender_id:
            cursor = conn.execute(
                "SELECT * FROM tenders WHERE portal_id = ? AND tender_id = ?",
                (portal_id, tender_id),
            )
            row = cursor.fetchone()
            if row is not None:
                existing = {FULL_FIELDS[i]: (row[i] or "") for i in range(len(FULL_FIELDS))}
                norm = _merge_tender(existing, norm)

        placeholders = ", ".join(["?"] * len(FULL_FIELDS))
        values = tuple(norm.get(f, "") for f in FULL_FIELDS)
        conn.execute(
            f"INSERT OR REPLACE INTO tenders VALUES ({placeholders})",
            values,
        )

    conn.commit()
    conn.close()


# ─── Analytics Query Helpers ─────────────────────────────────────────────────

def get_portal_stats(output_dir: Path = OUTPUT_DIR) -> list[dict]:
    """
    Per-portal counts: total tenders, tenders with details, latest scrape date.
    Returns: [{"portal_id": "cppp", "total": 500, "with_details": 300,
               "latest_scrape": "2026-04-01"}, ...]
    """
    conn = _get_db(output_dir)
    cursor = conn.execute("""
        SELECT
            portal_id,
            COUNT(*) AS total,
            SUM(CASE WHEN detail_scraped != '' AND detail_scraped != '0'
                     AND detail_scraped IS NOT NULL THEN 1 ELSE 0 END) AS with_details,
            MAX(COALESCE(NULLIF(scrape_date, ''), NULLIF(scraped_at, ''))) AS latest_scrape
        FROM tenders
        GROUP BY portal_id
        ORDER BY total DESC
    """)
    results = [
        {"portal_id": row[0], "total": row[1], "with_details": row[2],
         "latest_scrape": row[3] or ""}
        for row in cursor.fetchall()
    ]
    conn.close()
    return results


def get_category_breakdown(output_dir: Path = OUTPUT_DIR) -> list[dict]:
    """
    Count of tenders per industry_category.
    Returns: [{"industry_category": "IT Services", "count": 150}, ...]
    """
    conn = _get_db(output_dir)
    cursor = conn.execute("""
        SELECT industry_category, COUNT(*) AS cnt
        FROM tenders
        WHERE industry_category IS NOT NULL AND industry_category != ''
        GROUP BY industry_category
        ORDER BY cnt DESC
    """)
    results = [
        {"industry_category": row[0], "count": row[1]}
        for row in cursor.fetchall()
    ]
    conn.close()
    return results


def get_daily_counts(days: int = 30, output_dir: Path = OUTPUT_DIR) -> list[dict]:
    """
    Tenders scraped per day per portal for the last N days.
    Returns: [{"date": "2026-04-01", "portal_id": "cppp", "count": 50}, ...]
    """
    conn = _get_db(output_dir)
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor = conn.execute("""
        SELECT scrape_date, portal_id, COUNT(*) AS cnt
        FROM tenders
        WHERE scrape_date IS NOT NULL AND scrape_date != '' AND scrape_date >= ?
        GROUP BY scrape_date, portal_id
        ORDER BY scrape_date DESC, cnt DESC
    """, (cutoff,))
    results = [
        {"date": row[0], "portal_id": row[1], "count": row[2]}
        for row in cursor.fetchall()
    ]
    conn.close()
    return results


def get_duplicate_count(output_dir: Path = OUTPUT_DIR) -> int:
    """
    Count of tenders that exist in multiple portals (same title or ref_number).
    """
    conn = _get_db(output_dir)
    # Count by ref_number duplicates across portals
    cursor = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT ref_number
            FROM tenders
            WHERE ref_number IS NOT NULL AND ref_number != ''
            GROUP BY ref_number
            HAVING COUNT(DISTINCT portal_id) > 1
        )
    """)
    by_ref = cursor.fetchone()[0]
    # Count by title duplicates across portals (only for those without ref_number match)
    cursor = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT title
            FROM tenders
            WHERE title IS NOT NULL AND title != ''
              AND (ref_number IS NULL OR ref_number = '')
            GROUP BY title
            HAVING COUNT(DISTINCT portal_id) > 1
        )
    """)
    by_title = cursor.fetchone()[0]
    conn.close()
    return by_ref + by_title


def get_total_stats(output_dir: Path = OUTPUT_DIR) -> dict:
    """
    High-level dashboard stats.
    Returns: {"total_tenders": 5000, "portals_scraped": 15, "categories": 20,
              "date_range": "2026-03-01 to 2026-04-01"}
    """
    conn = _get_db(output_dir)

    total = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
    portals = conn.execute(
        "SELECT COUNT(DISTINCT portal_id) FROM tenders"
    ).fetchone()[0]
    categories = conn.execute(
        "SELECT COUNT(DISTINCT industry_category) FROM tenders "
        "WHERE industry_category IS NOT NULL AND industry_category != ''"
    ).fetchone()[0]
    date_row = conn.execute("""
        SELECT MIN(dt), MAX(dt) FROM (
            SELECT COALESCE(NULLIF(scrape_date, ''), NULLIF(scraped_at, '')) AS dt
            FROM tenders
        ) WHERE dt IS NOT NULL AND dt != ''
    """).fetchone()

    conn.close()

    min_date = date_row[0] or ""
    max_date = date_row[1] or ""
    date_range = f"{min_date} to {max_date}" if min_date else ""

    return {
        "total_tenders": total,
        "portals_scraped": portals,
        "categories": categories,
        "date_range": date_range,
    }


# ─── Snapshot / Daily Diff ────────────────────────────────────────────────────

class SnapshotStore:
    def __init__(self, base_dir: Path = SNAPSHOT_DIR):
        self.base_dir = base_dir

    def _path(self, portal_id: str) -> Path:
        return self.base_dir / f"{portal_id}.json"

    def load_known_ids(self, portal_id: str) -> set[str]:
        p = self._path(portal_id)
        if not p.exists():
            return set()
        data = json.loads(p.read_text(encoding="utf-8"))
        return {t.get("tender_id") or t.get("detail_url", "") for t in data if t}

    def save(self, portal_id: str, tenders: list[dict]):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._path(portal_id).write_text(
            json.dumps(tenders, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def diff(self, portal_id: str, new_tenders: list[dict]) -> list[dict]:
        known = self.load_known_ids(portal_id)
        return [
            t for t in new_tenders
            if (t.get("tender_id") or t.get("detail_url", "")) not in known
        ]


# ─── Run log ──────────────────────────────────────────────────────────────────

def write_run_log(entries: list[dict]):
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / "run_log.txt"
    ts   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(
                f"[{ts}] portal={e['portal_id']:15} "
                f"total={e['total']:5} new={e['new']:5} pages={e['pages']}\n"
            )
