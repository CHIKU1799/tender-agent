"""
Storage layer — CSV, JSON, SQLite exporters + snapshot diff store.

KEY FIX: Data now ACCUMULATES across scrape runs.
- SQLite is the master database (INSERT OR REPLACE deduplicates by tender_id)
- all_tenders.csv is rebuilt from SQLite after every run (never loses old data)
- Per-portal CSVs also accumulate (append mode with dedup)
"""
from __future__ import annotations
import csv
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger("core.storage")

OUTPUT_DIR   = Path("output")
SNAPSHOT_DIR = Path("output/snapshots")
LOG_DIR      = Path("logs")

# ── Complete field list ────────────────────────────────────────────────────────
FULL_FIELDS = [
    # Identity
    "portal_id", "portal_name", "tender_id", "ref_number",
    # Listing
    "title", "organisation", "state", "district",
    "published_date", "closing_date", "opening_date",
    "status", "detail_url", "scraped_at", "page_num",
    # Detail fields
    "detail_scraped",
    "tender_value_inr", "tender_fee_inr", "emd_inr", "emd_fee_type",
    "tender_type", "tender_category", "product_category",
    "form_of_contract", "payment_mode",
    "bid_submission_start", "bid_submission_end",
    "doc_download_start", "doc_download_end",
    "clarification_start", "clarification_end",
    "pre_bid_meeting", "bid_validity",
    "work_description", "two_stage_bid", "nda_allowed",
    "location", "pincode", "contact",
    "fee_payable_to", "emd_payable_to", "documents",
    # GeM
    "gem_category", "gem_quantity", "gem_consignee",
    # Awards
    "award_winner", "award_date", "award_amount", "aoc_no",
    # Source
    "source_website",
]

AWARD_FIELDS = [
    "portal_id", "portal_name", "tender_id", "ref_number",
    "title", "organisation", "state",
    "published_date", "closing_date",
    "tender_value_inr", "detail_url", "scraped_at",
    "award_winner", "award_date", "award_amount", "aoc_no",
]


def _normalise(tender: dict) -> dict:
    return {f: str(tender.get(f, "") or "").strip() for f in FULL_FIELDS}


# ── SQLite (master database — accumulates everything) ─────────────────────────

def _get_db(output_dir: Path = OUTPUT_DIR) -> sqlite3.Connection:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_dir / "tenders.db", timeout=30)
    conn.row_factory = sqlite3.Row

    # Build column definitions
    cols = []
    for f in FULL_FIELDS:
        cols.append(f"`{f}` TEXT" if f != "page_num" else "`page_num` INTEGER")

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS tenders (
            {', '.join(cols)},
            PRIMARY KEY (portal_id, tender_id)
        )
    """)

    # Add any new columns that may not exist yet (schema migration)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tenders)")}
    for f in FULL_FIELDS:
        if f not in existing:
            try:
                conn.execute(f"ALTER TABLE tenders ADD COLUMN `{f}` TEXT")
                log.info(f"[storage] Added column: {f}")
            except Exception:
                pass

    conn.commit()
    return conn


def save_sqlite(tenders: list[dict], output_dir: Path = OUTPUT_DIR):
    """Insert/replace tenders into SQLite master DB."""
    if not tenders:
        return
    conn = _get_db(output_dir)
    placeholders = ", ".join(["?"] * len(FULL_FIELDS))
    cols = ", ".join(f"`{f}`" for f in FULL_FIELDS)
    rows = [tuple(_normalise(t).get(f, "") for f in FULL_FIELDS) for t in tenders]
    conn.executemany(
        f"INSERT OR REPLACE INTO tenders ({cols}) VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    conn.close()
    log.info(f"[storage] SQLite: saved {len(tenders)} tenders")


def load_all_from_sqlite(output_dir: Path = OUTPUT_DIR) -> list[dict]:
    """Load ALL accumulated tenders from SQLite master DB."""
    db_path = output_dir / "tenders.db"
    if not db_path.exists():
        return []
    conn = _get_db(output_dir)
    rows = conn.execute("SELECT * FROM tenders ORDER BY scraped_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_portal_from_sqlite(portal_id: str, output_dir: Path = OUTPUT_DIR) -> list[dict]:
    """Load tenders for one portal from SQLite."""
    db_path = output_dir / "tenders.db"
    if not db_path.exists():
        return []
    conn = _get_db(output_dir)
    rows = conn.execute(
        "SELECT * FROM tenders WHERE portal_id=? ORDER BY scraped_at DESC",
        (portal_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_db_stats(output_dir: Path = OUTPUT_DIR) -> dict:
    """Return stats about the accumulated database."""
    db_path = output_dir / "tenders.db"
    if not db_path.exists():
        return {"total": 0, "by_portal": {}, "by_status": {}, "by_state": {}}
    conn = _get_db(output_dir)
    total = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
    by_portal = dict(conn.execute(
        "SELECT portal_id, COUNT(*) FROM tenders GROUP BY portal_id"
    ).fetchall())
    by_status = dict(conn.execute(
        "SELECT status, COUNT(*) FROM tenders GROUP BY status"
    ).fetchall())
    by_state = dict(conn.execute(
        "SELECT state, COUNT(*) FROM tenders WHERE state!='' GROUP BY state ORDER BY COUNT(*) DESC LIMIT 30"
    ).fetchall())
    awarded = conn.execute(
        "SELECT COUNT(*) FROM tenders WHERE award_winner!='' OR award_date!=''"
    ).fetchone()[0]
    conn.close()
    return {
        "total": total, "by_portal": by_portal,
        "by_status": by_status, "by_state": by_state,
        "awarded": awarded,
    }


# ── CSV (rebuilt from SQLite after every run) ─────────────────────────────────

def save_csv(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR) -> Path:
    """Save per-portal CSV — accumulates (deduped via SQLite first)."""
    # First persist to SQLite
    save_sqlite(tenders, output_dir)

    # Rebuild CSV from SQLite so it's always complete and deduped
    output_dir.mkdir(parents=True, exist_ok=True)
    path      = output_dir / f"{portal_id}_tenders.csv"
    all_rows  = load_portal_from_sqlite(portal_id, output_dir)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([_normalise(r) for r in all_rows])

    log.info(f"[storage] {portal_id}_tenders.csv → {len(all_rows)} rows (accumulated)")
    return path


def save_combined_csv(new_tenders: list[dict], output_dir: Path = OUTPUT_DIR) -> Path:
    """
    Rebuild all_tenders.csv from the FULL SQLite database.
    This means every scrape run ADDS to the CSV — old data is never lost.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path     = output_dir / "all_tenders.csv"
    all_rows = load_all_from_sqlite(output_dir)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([_normalise(r) for r in all_rows])

    log.info(f"[storage] all_tenders.csv → {len(all_rows)} rows total (all-time)")
    return path


def save_awards_csv(tenders: list[dict], output_dir: Path = OUTPUT_DIR) -> Path:
    """Save/update awarded tenders CSV from SQLite."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "awarded_tenders.csv"

    db_path = output_dir / "tenders.db"
    if db_path.exists():
        conn    = _get_db(output_dir)
        awarded = conn.execute(
            "SELECT * FROM tenders WHERE award_winner!='' OR award_date!='' ORDER BY award_date DESC"
        ).fetchall()
        conn.close()
        rows = [dict(r) for r in awarded]
    else:
        rows = [t for t in tenders if t.get("award_winner") or t.get("award_date")]

    if not rows:
        return None

    def norm(t):
        return {f: str(t.get(f, "") or "").strip() for f in AWARD_FIELDS}

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=AWARD_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([norm(r) for r in rows])

    log.info(f"[storage] awarded_tenders.csv → {len(rows)} rows")
    return path


def save_json(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR) -> Path:
    """Save per-portal JSON — always the full accumulated set."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path     = output_dir / f"{portal_id}_tenders.json"
    all_rows = load_portal_from_sqlite(portal_id, output_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False, default=str)
    return path


# ── Snapshot / Diff ───────────────────────────────────────────────────────────

class SnapshotStore:
    def __init__(self, base_dir: Path = SNAPSHOT_DIR):
        self.base_dir = base_dir

    def _path(self, portal_id: str) -> Path:
        return self.base_dir / f"{portal_id}.json"

    def load_known_ids(self, portal_id: str) -> set[str]:
        # Use SQLite as source of truth for known IDs
        conn    = _get_db()
        rows    = conn.execute(
            "SELECT tender_id, detail_url FROM tenders WHERE portal_id=?", (portal_id,)
        ).fetchall()
        conn.close()
        return {r[0] or r[1] for r in rows if r[0] or r[1]}

    def save(self, portal_id: str, tenders: list[dict]):
        # SQLite is the master — snapshot just for backward compat
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._path(portal_id).write_text(
            json.dumps(tenders[-500:], indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def diff(self, portal_id: str, new_tenders: list[dict]) -> list[dict]:
        known = self.load_known_ids(portal_id)
        return [
            t for t in new_tenders
            if (t.get("tender_id") or t.get("detail_url", "")) not in known
        ]


# ── Run log ───────────────────────────────────────────────────────────────────

def write_run_log(entries: list[dict]):
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / "run_log.txt"
    ts   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(
                f"[{ts}] portal={e.get('portal_id','?'):20} "
                f"total={e.get('total',0):5} new={e.get('new',0):5} "
                f"pages={e.get('pages',0)}\n"
            )
