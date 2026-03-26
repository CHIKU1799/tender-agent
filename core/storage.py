"""
Storage layer — CSV, JSON, SQLite exporters + snapshot diff store.
All formats share the same FULL_FIELDS schema so CSV always has every column.
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
]


def _normalise(tender: dict) -> dict:
    """Ensure every field exists (empty string if missing)."""
    return {f: str(tender.get(f, "") or "").strip() for f in FULL_FIELDS}


# ─── CSV ──────────────────────────────────────────────────────────────────────

def save_csv(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path   = output_dir / f"{portal_id}_tenders.csv"
    is_new = not path.exists() or path.stat().st_size == 0
    rows   = [_normalise(t) for t in tenders]
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        # utf-8-sig adds BOM so Excel opens correctly
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(rows)
    return path


def save_combined_csv(all_tenders: list[dict], output_dir: Path = OUTPUT_DIR) -> Path:
    """Single CSV with all portals combined."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path   = output_dir / "all_tenders.csv"
    rows   = [_normalise(t) for t in all_tenders]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FULL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


# ─── JSON ─────────────────────────────────────────────────────────────────────

def save_json(tenders: list[dict], portal_id: str, output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{portal_id}_tenders.json"
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
    return conn


def save_sqlite(tenders: list[dict], output_dir: Path = OUTPUT_DIR):
    if not tenders:
        return
    conn = _get_db(output_dir)
    placeholders = ", ".join(["?"] * len(FULL_FIELDS))
    rows = [
        tuple(_normalise(t).get(f, "") for f in FULL_FIELDS)
        for t in tenders
    ]
    conn.executemany(
        f"INSERT OR REPLACE INTO tenders VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    conn.close()


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
