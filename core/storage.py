"""
Output layer — CSV, JSON, SQLite exporters + snapshot store for daily diff.
"""
from __future__ import annotations
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

OUTPUT_DIR   = Path("output")
SNAPSHOT_DIR = Path("output/snapshots")
LOG_DIR      = Path("logs")

CSV_FIELDS = [
    "portal_id", "tender_id", "ref_number", "title", "organisation",
    "published_date", "closing_date", "opening_date", "status",
    "detail_url", "scraped_at", "page_num",
]

DETAIL_FIELDS = CSV_FIELDS + [
    "tender_value", "tender_fee", "emd", "tender_type", "tender_category",
    "product_category", "form_of_contract", "payment_mode",
    "bid_submission_start", "bid_submission_end",
    "document_sale_start", "document_sale_end",
    "location", "pincode", "contact", "documents",
]


# ─── CSV ─────────────────────────────────────────────────────────────────────

def save_csv(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{portal_id}_tenders.csv"
    is_new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(tenders)
    return path


# ─── JSON ────────────────────────────────────────────────────────────────────

def save_json(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{portal_id}_tenders.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tenders, f, indent=2, ensure_ascii=False)
    return path


# ─── SQLite ──────────────────────────────────────────────────────────────────

def _get_db(output_dir: Path = OUTPUT_DIR) -> sqlite3.Connection:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output_dir / "tenders.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenders (
            portal_id       TEXT NOT NULL,
            tender_id       TEXT NOT NULL,
            ref_number      TEXT,
            title           TEXT,
            organisation    TEXT,
            published_date  TEXT,
            closing_date    TEXT,
            opening_date    TEXT,
            status          TEXT,
            detail_url      TEXT,
            scraped_at      TEXT,
            page_num        INTEGER,
            PRIMARY KEY (portal_id, tender_id)
        )
    """)
    conn.commit()
    return conn


def save_sqlite(tenders: list[dict], output_dir: Path = OUTPUT_DIR):
    if not tenders:
        return
    conn = _get_db(output_dir)
    rows = [
        (
            t.get("portal_id", ""), t.get("tender_id", ""), t.get("ref_number", ""),
            t.get("title", ""), t.get("organisation", ""), t.get("published_date", ""),
            t.get("closing_date", ""), t.get("opening_date", ""), t.get("status", ""),
            t.get("detail_url", ""), t.get("scraped_at", ""), t.get("page_num", 0),
        )
        for t in tenders
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO tenders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    conn.close()


# ─── Snapshot / Daily Diff ───────────────────────────────────────────────────

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
            json.dumps(tenders, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def diff(self, portal_id: str, new_tenders: list[dict]) -> list[dict]:
        known = self.load_known_ids(portal_id)
        return [
            t for t in new_tenders
            if (t.get("tender_id") or t.get("detail_url", "")) not in known
        ]


# ─── Run log ─────────────────────────────────────────────────────────────────

def write_run_log(entries: list[dict]):
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / "run_log.txt"
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(
                f"[{ts}] portal={e['portal_id']} "
                f"total={e['total']} new={e['new']} pages={e['pages']}\n"
            )
