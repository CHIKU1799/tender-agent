"""
Data Cleaning Pipeline for Tender Agent.

Handles:
  - Date normalization  (DD/MM/YYYY, MM-DD-YYYY, "31 Mar 2026", etc. → ISO 8601)
  - Monetary value parsing (₹1.5 Cr, 15,00,000, "1.5 lakh" → plain integer rupees)
  - Organisation name normalization (abbreviation expansion, title-case, de-dup)
  - HTML / Unicode artifact removal
  - Whitespace & encoding cleanup
  - Status normalization
  - Duplicate detection (by tender_id + portal, or by fuzzy title match)
  - Field completeness scoring
  - Bulk clean + export helpers

Usage:
    from core.cleaner import TenderCleaner
    cleaner = TenderCleaner()
    cleaned = cleaner.clean_batch(raw_tenders)
    report  = cleaner.report(raw_tenders, cleaned)
"""
from __future__ import annotations

import html
import logging
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

log = logging.getLogger("core.cleaner")

# ── Date patterns — ordered from most specific to least ──────────────────────
_DATE_PATTERNS = [
    # ISO already
    (r"^(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}:\d{2})?$", "%Y-%m-%d"),
    # DD/MM/YYYY  or  DD-MM-YYYY
    (r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$", None),      # handled specially
    # DD-Mon-YYYY  e.g. "31-Mar-2026"
    (r"^(\d{1,2})[\-\s]([A-Za-z]{3,9})[\-\s](\d{4})$", None),  # handled specially
    # Mon DD, YYYY  e.g. "Mar 31, 2026"
    (r"^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$", None),
    # DD/MM/YY
    (r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2})$", None),
    # YYYY/MM/DD
    (r"^(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})$", "%Y/%m/%d"),
    # Compact YYYYMMDD
    (r"^(\d{8})$", "%Y%m%d"),
]

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

# ── Value multipliers ─────────────────────────────────────────────────────────
_VALUE_MULTIPLIERS = {
    "cr":     1_00_00_000,
    "crore":  1_00_00_000,
    "crores": 1_00_00_000,
    "l":      1_00_000,
    "lac":    1_00_000,
    "lacs":   1_00_000,
    "lakh":   1_00_000,
    "lakhs":  1_00_000,
    "k":      1_000,
    "thousand": 1_000,
    "mn":     10_00_000,
    "million": 10_00_000,
}

# ── Organisation normalisations ───────────────────────────────────────────────
_ORG_REPLACEMENTS = [
    (r"\bGOVT\.?\b", "Government"),
    (r"\bGOV\.?\b",  "Government"),
    (r"\bDEPT\.?\b", "Department"),
    (r"\bDEP\.?\b",  "Department"),
    (r"\bMIN\.?\b",  "Ministry"),
    (r"\bDIV\.?\b",  "Division"),
    (r"\bHQ\.?\b",   "Headquarters"),
    (r"\bO/O\b",     "Office Of"),
    (r"\bO\.O\b",    "Office Of"),
    (r"\bNO\.?\s*(\d+)\b", r"No.\1"),
    (r"\s{2,}", " "),
]

# ── Status normalisation map ──────────────────────────────────────────────────
_STATUS_MAP = {
    "active":   "Active",
    "open":     "Active",
    "live":     "Active",
    "new":      "Active",
    "ongoing":  "Active",
    "closed":   "Archive",
    "expired":  "Archive",
    "archive":  "Archive",
    "archived": "Archive",
    "past":     "Archive",
    "awarded":  "Awarded",
    "award":    "Awarded",
    "finalized": "Awarded",
    "finalised": "Awarded",
    "won":      "Awarded",
    "cancelled": "Cancelled",
    "canceled":  "Cancelled",
    "withdrawn": "Cancelled",
    "deleted":   "Cancelled",
}

# ── HTML/Unicode noise patterns ───────────────────────────────────────────────
_HTML_TAG_RE    = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")
_CONTROL_RE     = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class TenderCleaner:
    """Stateful cleaner — call clean_batch() on a list of tender dicts."""

    def __init__(
        self,
        fuzzy_dedup_threshold: float = 0.92,
        remove_incomplete: bool = False,
        min_completeness: float = 0.20,
    ):
        """
        Args:
            fuzzy_dedup_threshold: SequenceMatcher ratio above which two tenders
                                   are considered duplicates (0–1, default 0.92).
            remove_incomplete:     Drop tenders below min_completeness score.
            min_completeness:      Fraction of non-empty fields required to keep
                                   a tender (only used when remove_incomplete=True).
        """
        self.fuzzy_dedup_threshold = fuzzy_dedup_threshold
        self.remove_incomplete     = remove_incomplete
        self.min_completeness      = min_completeness

        # Telemetry
        self._stats: dict[str, int] = {}

    # ─── Public API ───────────────────────────────────────────────────────────

    def clean_batch(self, tenders: list[dict]) -> list[dict]:
        """Clean a list of tender dicts. Returns cleaned list (may be shorter)."""
        self._stats = {
            "input":            len(tenders),
            "date_fixed":       0,
            "value_fixed":      0,
            "org_normalised":   0,
            "status_fixed":     0,
            "html_stripped":    0,
            "exact_dupes":      0,
            "fuzzy_dupes":      0,
            "incomplete_dropped": 0,
        }

        cleaned = [self._clean_one(t) for t in tenders]
        cleaned = self._dedup(cleaned)

        if self.remove_incomplete:
            before = len(cleaned)
            cleaned = [t for t in cleaned if self._completeness(t) >= self.min_completeness]
            self._stats["incomplete_dropped"] = before - len(cleaned)

        self._stats["output"] = len(cleaned)
        log.info(
            f"[cleaner] {self._stats['input']} → {self._stats['output']} "
            f"(dates:{self._stats['date_fixed']} values:{self._stats['value_fixed']} "
            f"exact_dupes:{self._stats['exact_dupes']} fuzzy_dupes:{self._stats['fuzzy_dupes']})"
        )
        return cleaned

    def clean_one(self, tender: dict) -> dict:
        """Clean a single tender dict."""
        return self._clean_one(tender)

    def report(self, original: list[dict], cleaned: list[dict]) -> dict:
        """Return a summary report of what was changed."""
        return {
            "input_count":    len(original),
            "output_count":   len(cleaned),
            "removed":        len(original) - len(cleaned),
            "stats":          dict(self._stats),
            "completeness":   {
                "mean":  round(sum(self._completeness(t) for t in cleaned) / max(len(cleaned), 1), 3),
                "below_50pct": sum(1 for t in cleaned if self._completeness(t) < 0.5),
            },
        }

    # ─── Field cleaners ───────────────────────────────────────────────────────

    def _clean_one(self, raw: dict) -> dict:
        t = dict(raw)  # shallow copy

        # 1. Strip HTML / Unicode noise from ALL string fields
        for key, val in t.items():
            if isinstance(val, str):
                cleaned_val = self._strip_html(val)
                if cleaned_val != val:
                    self._stats["html_stripped"] += 1
                t[key] = cleaned_val

        # 2. Dates
        for date_field in ("published_date", "closing_date", "opening_date",
                           "bid_submission_start", "bid_submission_end",
                           "doc_download_start", "doc_download_end",
                           "award_date", "pre_bid_meeting"):
            val = t.get(date_field, "")
            if val:
                fixed = self._parse_date(val)
                if fixed and fixed != val:
                    t[date_field] = fixed
                    self._stats["date_fixed"] += 1

        # 3. Monetary values
        for val_field in ("tender_value_inr", "tender_fee_inr", "emd_inr", "award_amount"):
            val = t.get(val_field, "")
            if val:
                fixed = self._parse_value(val)
                if fixed is not None and str(fixed) != val:
                    t[val_field] = str(fixed)
                    self._stats["value_fixed"] += 1

        # 4. Organisation name
        org = t.get("organisation", "")
        if org:
            fixed_org = self._normalise_org(org)
            if fixed_org != org:
                t["organisation"] = fixed_org
                self._stats["org_normalised"] += 1

        # 5. Status
        status = t.get("status", "")
        if status:
            fixed_status = _STATUS_MAP.get(status.strip().lower(), status)
            if fixed_status != status:
                t["status"] = fixed_status
                self._stats["status_fixed"] += 1
        elif not status:
            # Infer status from closing date
            t["status"] = self._infer_status(t)

        # 6. Title cleanup
        title = t.get("title", "")
        if title:
            t["title"] = self._clean_title(title)

        # 7. Tender ID — strip whitespace
        for id_field in ("tender_id", "ref_number", "aoc_no"):
            if t.get(id_field):
                t[id_field] = t[id_field].strip()

        # 8. Add completeness score (0–1)
        t["_completeness"] = round(self._completeness(t), 3)

        return t

    # ─── Date parser ──────────────────────────────────────────────────────────

    def _parse_date(self, raw: str) -> Optional[str]:
        """Return ISO date string 'YYYY-MM-DD' or None."""
        s = raw.strip()
        if not s or s in ("-", "N/A", "NA", "—"):
            return ""

        # Remove time component if present for initial matching
        s_date = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*(AM|PM))?$", "", s, flags=re.IGNORECASE).strip()

        # Try strptime formats
        for fmt in (
            "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
            "%Y/%m/%d", "%Y%m%d",
            "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y",
            "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
            "%d/%m/%y", "%d-%m-%y",
        ):
            try:
                dt = datetime.strptime(s_date, fmt)
                if dt.year < 2000:
                    dt = dt.replace(year=dt.year + 2000)
                if dt.year > 2040 or dt.year < 2000:
                    continue
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Manual DD/MM or DD-Mon patterns
        m = re.match(r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})$", s_date)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            try:
                return datetime(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                try:
                    return datetime(y, d, mo).strftime("%Y-%m-%d")
                except ValueError:
                    pass

        m = re.match(r"^(\d{1,2})[\-\s/]([A-Za-z]{3,9})[\-\s/](\d{4})$", s_date)
        if m:
            d  = int(m.group(1))
            mo = _MONTH_MAP.get(m.group(2).lower())
            y  = int(m.group(3))
            if mo:
                try:
                    return datetime(y, mo, d).strftime("%Y-%m-%d")
                except ValueError:
                    pass

        return None  # Could not parse — leave as-is

    # ─── Value parser ─────────────────────────────────────────────────────────

    def _parse_value(self, raw: str) -> Optional[int]:
        """Return integer rupee value, or None if unparseable."""
        s = raw.strip()
        if not s or s in ("-", "N/A", "NA", "—"):
            return None

        # Strip currency symbols, commas, spaces
        s = re.sub(r"[₹Rs\.,$\s,]", "", s, flags=re.IGNORECASE)

        # Match  number + optional unit  e.g. "1.5Cr", "25L", "12000"
        m = re.match(r"^([\d]+\.?\d*)\s*([A-Za-z]*)$", s)
        if not m:
            return None

        try:
            number = float(m.group(1))
        except ValueError:
            return None

        unit = m.group(2).lower().rstrip(".")
        multiplier = _VALUE_MULTIPLIERS.get(unit, 1)

        return int(number * multiplier)

    # ─── Org normaliser ───────────────────────────────────────────────────────

    def _normalise_org(self, raw: str) -> str:
        """Normalise organisation name."""
        s = raw.strip()
        # Title-case preserving known abbreviations
        # First apply replacement patterns
        for pattern, repl in _ORG_REPLACEMENTS:
            s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
        # Convert runs of all-caps words to title case (but preserve 2-4 char acronyms)
        words = s.split()
        out = []
        for w in words:
            if len(w) <= 4 and w.isupper():
                out.append(w)  # Keep acronym: NTPC, ONGC, etc.
            elif w.isupper() and len(w) > 4:
                out.append(w.title())
            else:
                out.append(w)
        return " ".join(out).strip()

    # ─── HTML stripper ────────────────────────────────────────────────────────

    def _strip_html(self, raw: str) -> str:
        """Remove HTML tags, decode entities, normalise whitespace."""
        if not raw:
            return raw
        # Decode HTML entities (&amp; &nbsp; etc.)
        s = html.unescape(raw)
        # Remove HTML/XML tags
        s = _HTML_TAG_RE.sub(" ", s)
        # Remove control characters
        s = _CONTROL_RE.sub("", s)
        # Normalise unicode (NFKC — converts fullwidth chars, etc.)
        s = unicodedata.normalize("NFKC", s)
        # Collapse whitespace
        s = _MULTI_SPACE_RE.sub(" ", s).strip()
        return s

    # ─── Title cleaner ────────────────────────────────────────────────────────

    def _clean_title(self, raw: str) -> str:
        s = self._strip_html(raw)
        # Remove leading/trailing punctuation noise
        s = re.sub(r"^[^\w₹]+|[^\w₹.)\]]+$", "", s).strip()
        # Collapse internal double spaces
        s = re.sub(r"  +", " ", s)
        return s

    # ─── Status inference ────────────────────────────────────────────────────

    def _infer_status(self, t: dict) -> str:
        """Guess status from closing date."""
        closing = t.get("closing_date", "")
        if not closing:
            return "Active"
        try:
            dt = datetime.strptime(closing[:10], "%Y-%m-%d")
            if dt < datetime.utcnow():
                return "Archive"
            return "Active"
        except Exception:
            return "Active"

    # ─── Completeness score ───────────────────────────────────────────────────

    # Core fields for completeness calculation
    _CORE_FIELDS = [
        "title", "organisation", "published_date", "closing_date",
        "tender_id", "status", "portal_id",
    ]
    _BONUS_FIELDS = [
        "tender_value_inr", "tender_type", "location", "detail_url",
    ]

    def _completeness(self, t: dict) -> float:
        core_score  = sum(1 for f in self._CORE_FIELDS  if t.get(f)) / len(self._CORE_FIELDS)
        bonus_score = sum(1 for f in self._BONUS_FIELDS if t.get(f)) / len(self._BONUS_FIELDS)
        return round(core_score * 0.8 + bonus_score * 0.2, 3)

    # ─── Deduplication ───────────────────────────────────────────────────────

    def _dedup(self, tenders: list[dict]) -> list[dict]:
        """Remove exact and near-duplicate tenders."""
        seen_ids:    set[str] = set()
        seen_titles: list[str] = []
        result: list[dict] = []

        for t in tenders:
            # 1. Exact ID dedup
            uid = f"{t.get('portal_id','')}::{t.get('tender_id','') or t.get('ref_number','')}"
            if uid != "::" and uid in seen_ids:
                self._stats["exact_dupes"] += 1
                continue
            seen_ids.add(uid)

            # 2. Fuzzy title dedup (within same portal)
            title = t.get("title", "").strip().lower()
            portal = t.get("portal_id", "")
            if title and len(title) > 20:
                is_dupe = False
                for seen_title in seen_titles[-300:]:  # Check last 300 (performance)
                    if seen_title.startswith(portal + "::"):
                        ratio = SequenceMatcher(
                            None,
                            title,
                            seen_title[len(portal)+2:],
                            autojunk=False,
                        ).ratio()
                        if ratio >= self.fuzzy_dedup_threshold:
                            self._stats["fuzzy_dupes"] += 1
                            is_dupe = True
                            break
                if is_dupe:
                    continue
                seen_titles.append(f"{portal}::{title}")

            result.append(t)

        return result


# ─── Convenience functions ────────────────────────────────────────────────────

def clean_tenders(tenders: list[dict], **kwargs) -> list[dict]:
    """Shorthand: clean_tenders(raw_list) → cleaned list."""
    return TenderCleaner(**kwargs).clean_batch(tenders)


def clean_and_report(tenders: list[dict], **kwargs) -> tuple[list[dict], dict]:
    """Returns (cleaned_list, report_dict)."""
    cleaner = TenderCleaner(**kwargs)
    cleaned = cleaner.clean_batch(tenders)
    report  = cleaner.report(tenders, cleaned)
    return cleaned, report


def load_and_clean_csv(csv_path: str, **kwargs) -> tuple[list[dict], dict]:
    """Load a CSV, clean it, and return (cleaned_rows, report)."""
    import csv
    from pathlib import Path
    rows = []
    p = Path(csv_path)
    if not p.exists():
        return [], {"error": f"File not found: {csv_path}"}
    with open(p, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return clean_and_report(rows, **kwargs)
