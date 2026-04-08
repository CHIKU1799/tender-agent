#!/usr/bin/env python3
"""
Gujarat Tenders Scraper — Two Sources
══════════════════════════════════════
1. gujarattenders.in  — Aggregator, ~1,980 active tenders, ASP.NET postback pagination
2. gil.gujarat.gov.in — Gujarat Informatics Ltd, ~22,000+ tenders, ASP.NET GridView

Both are ASP.NET WebForms sites requiring ViewState-based POST requests for pagination.

Usage:
    python scrape_gujarat.py                    # scrape both sites
    python scrape_gujarat.py --source gt        # gujarattenders.in only
    python scrape_gujarat.py --source gil       # gil.gujarat.gov.in only
    python scrape_gujarat.py --max-pages 50     # cap pages
    python scrape_gujarat.py --skip-details     # listings only (faster)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from dotenv import load_dotenv
load_dotenv()

Path("logs").mkdir(exist_ok=True)
Path("output").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/gujarat_scrape.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("gujarat")

from core.storage import save_csv, save_json, save_sqlite, save_daily_snapshot, OUTPUT_DIR
from ai.classifier import classify_tender_local

# ── Shared ASP.NET helpers ──────────────────────────────────────────────────

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    log.error("Install: pip install requests beautifulsoup4")
    sys.exit(1)


def extract_viewstate(html: str) -> dict:
    """Extract ASP.NET hidden form fields from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                  "__VIEWSTATEENCRYPTED", "__PREVIOUSPAGE"]:
        tag = soup.find("input", {"name": name})
        if tag:
            fields[name] = tag.get("value", "")
    return fields


# ═════════════════════════════════════════════════════════════════════════════
# Source 1: gujarattenders.in
# ═════════════════════════════════════════════════════════════════════════════

GT_BASE = "https://www.gujarattenders.in"
GT_URL = GT_BASE + "/default.aspx"
GT_DETAIL_URL = GT_BASE + "/Tenderdetailbrief.aspx?SrNo={sr_no}"


def scrape_gujarattenders(max_pages: int | None, fetch_details: bool) -> list[dict]:
    """Scrape gujarattenders.in using requests + BeautifulSoup."""
    log.info("═══ gujarattenders.in ═══")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })

    # Initial GET
    log.info("[gt] Loading homepage...")
    resp = session.get(GT_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    vs = extract_viewstate(resp.text)

    # Check total count
    hidden_total = soup.find("input", {"id": lambda x: x and "HdnTotalCount" in x})
    total_count = int(hidden_total["value"]) if hidden_total else 0
    log.info(f"[gt] Total tenders reported: {total_count}")

    all_tenders = []
    page = 1
    consecutive_empty = 0

    while True:
        if max_pages and page > max_pages:
            break

        # Parse tenders from current page
        tenders_on_page = _gt_parse_page(soup, page)

        if not tenders_on_page:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                log.info(f"[gt] No tenders on page {page}, stopping.")
                break
        else:
            consecutive_empty = 0

        all_tenders.extend(tenders_on_page)

        if page % 10 == 0 or page == 1:
            log.info(f"[gt] Page {page} — {len(tenders_on_page)} tenders (total: {len(all_tenders)})")

        # Fetch details for each tender
        if fetch_details and tenders_on_page:
            for t in tenders_on_page:
                sr_no = t.get("tender_id", "")
                if sr_no:
                    detail = _gt_fetch_detail(session, sr_no)
                    if detail:
                        for k, v in detail.items():
                            if v and not t.get(k):
                                t[k] = v
                        t["detail_scraped"] = "True"

        # Navigate to next page via __doPostBack
        page += 1
        post_data = dict(vs)
        post_data["__EVENTTARGET"] = "ctl00$ContentPlaceHolder1$Nextbutton"
        post_data["__EVENTARGUMENT"] = ""

        try:
            resp = session.post(GT_URL, data=post_data, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            vs = extract_viewstate(resp.text)
        except Exception as e:
            log.error(f"[gt] Page {page} navigation failed: {e}")
            break

        # Save incrementally every 50 pages
        if page % 50 == 0 and all_tenders:
            save_sqlite(all_tenders, OUTPUT_DIR)
            log.info(f"[gt] Checkpoint: {len(all_tenders)} tenders saved")

        time.sleep(0.5)  # Be polite

    log.info(f"[gt] Done: {len(all_tenders)} tenders from {page - 1} pages")
    return all_tenders


def _gt_parse_page(soup: BeautifulSoup, page_num: int) -> list[dict]:
    """Parse tender rows from gujarattenders.in listing page.

    Each tender spans 6 table rows; only the 10-cell row contains all data.
    Layout: Cell1=serial, Cell3=sector, Cell4=value, Cell5=location,
            Cell6=ref_no, Cell7=closing_date, Cell9=title.
    """
    tenders = []
    grid = soup.find("table", {"id": lambda x: x and "GRDFreshTender" in x})
    if not grid:
        return tenders

    seen_ids = set()
    for row in grid.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 10:
            continue

        # Extract SrNo from detail link
        link = row.find("a", href=lambda h: h and "SrNo=" in h)
        sr_no = ""
        if link:
            m = re.search(r"SrNo=(\d+)", link.get("href", ""))
            if m:
                sr_no = m.group(1)
        if not sr_no or sr_no in seen_ids:
            continue
        seen_ids.add(sr_no)

        title = cells[9].get_text(strip=True)[:500] if len(cells) > 9 else ""
        sector = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        value = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        location = cells[5].get_text(strip=True) if len(cells) > 5 else ""
        ref_no = cells[6].get_text(strip=True) if len(cells) > 6 else ""
        closing_raw = cells[7].get_text(strip=True) if len(cells) > 7 else ""
        # Parse closing date: "22 - Apr - 2026|19Days to go" → "22 - Apr - 2026"
        closing_date = closing_raw.split("|")[0].strip() if closing_raw else ""

        tenders.append({
            "portal_id": "gujarattenders",
            "portal_name": "Gujarat Tenders (gujarattenders.in)",
            "tender_id": sr_no,
            "ref_number": ref_no,
            "title": title,
            "product_category": sector,
            "closing_date": closing_date,
            "tender_value_inr": value,
            "status": "Active",
            "detail_url": f"{GT_BASE}/Tenderdetailbrief.aspx?SrNo={sr_no}" if sr_no else "",
            "scraped_at": datetime.now().isoformat(),
            "page_num": str(page_num),
            "detail_scraped": "False",
            "location": location or "Gujarat",
        })

    return tenders


def _gt_fetch_detail(session: requests.Session, sr_no: str) -> dict | None:
    """Fetch detail page for a single tender from gujarattenders.in."""
    try:
        url = GT_DETAIL_URL.format(sr_no=sr_no)
        resp = session.get(url, timeout=20)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        detail = {}

        # Extract key-value pairs from the detail page
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                value = cells[1].get_text(strip=True)
                if not value:
                    continue

                if "company" in label or "organisation" in label:
                    detail["organisation"] = value
                elif "product" in label or "description" in label:
                    detail["work_description"] = value[:2000]
                elif "location" in label or "city" in label:
                    detail["location"] = value
                elif "tender value" in label or "estimated" in label:
                    detail["tender_value_inr"] = value
                elif "emd" in label:
                    detail["emd_inr"] = value
                elif "document fee" in label or "tender fee" in label:
                    detail["tender_fee_inr"] = value
                elif "closing" in label or "last date" in label:
                    detail["closing_date"] = value
                elif "opening" in label:
                    detail["opening_date"] = value
                elif "ref" in label or "reference" in label:
                    detail["ref_number"] = value
                elif "category" in label or "industry" in label:
                    detail["product_category"] = value
                elif "published" in label:
                    detail["published_date"] = value

        time.sleep(0.3)
        return detail

    except Exception as e:
        log.warning(f"[gt] Detail error sr={sr_no}: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Source 2: gil.gujarat.gov.in
# ═════════════════════════════════════════════════════════════════════════════

GIL_BASE = "https://gil.gujarat.gov.in"
GIL_URL = GIL_BASE + "/tenders"
GIL_DETAIL_URL = GIL_BASE + "/TenderDetails.aspx?TenID={ten_id}"


def scrape_gil_gujarat(max_pages: int | None, fetch_details: bool) -> list[dict]:
    """Scrape gil.gujarat.gov.in using requests + BeautifulSoup."""
    log.info("═══ gil.gujarat.gov.in ═══")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })

    # Initial GET
    log.info("[gil] Loading tenders page...")
    resp = session.get(GIL_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    vs = extract_viewstate(resp.text)

    all_tenders = []
    page = 1
    consecutive_empty = 0

    while True:
        if max_pages and page > max_pages:
            break

        tenders_on_page = _gil_parse_page(soup, page)

        if not tenders_on_page:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                log.info(f"[gil] No tenders on page {page}, stopping.")
                break
        else:
            consecutive_empty = 0

        all_tenders.extend(tenders_on_page)

        if page % 10 == 0 or page == 1:
            log.info(f"[gil] Page {page} — {len(tenders_on_page)} tenders (total: {len(all_tenders)})")

        # Fetch details
        if fetch_details and tenders_on_page:
            for t in tenders_on_page:
                ten_id = t.get("tender_id", "")
                if ten_id:
                    detail = _gil_fetch_detail(session, ten_id)
                    if detail:
                        for k, v in detail.items():
                            if v and not t.get(k):
                                t[k] = v
                        t["detail_scraped"] = "True"

        # Navigate to next page
        page += 1
        post_data = dict(vs)
        post_data["__EVENTTARGET"] = "ctl00$body$gvTenderList"
        post_data["__EVENTARGUMENT"] = f"Page${page}"

        try:
            resp = session.post(GIL_URL, data=post_data, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            vs = extract_viewstate(resp.text)
        except Exception as e:
            log.error(f"[gil] Page {page} navigation failed: {e}")
            break

        # Incremental save
        if page % 50 == 0 and all_tenders:
            save_sqlite(all_tenders, OUTPUT_DIR)
            log.info(f"[gil] Checkpoint: {len(all_tenders)} tenders saved")

        time.sleep(0.5)

    log.info(f"[gil] Done: {len(all_tenders)} tenders from {page - 1} pages")
    return all_tenders


def _gil_parse_page(soup: BeautifulSoup, page_num: int) -> list[dict]:
    """Parse tender rows from gil.gujarat.gov.in."""
    tenders = []
    grid = soup.find("table", {"id": lambda x: x and "gvTenderList" in x})
    if not grid:
        return tenders

    for row in grid.find_all("tr")[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        # Check for pager row (contains page links, not data)
        if row.find("table"):
            continue

        sr_no = cells[0].get_text(strip=True)
        title = cells[1].get_text(strip=True)

        # Extract TenID from detail link
        link = row.find("a", href=lambda h: h and "TenID=" in h)
        ten_id = ""
        if link:
            m = re.search(r"TenID=(\d+)", link.get("href", ""))
            if m:
                ten_id = m.group(1)

        # Dates from cells
        pre_bid = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        closing_date = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        opening_date = cells[5].get_text(strip=True) if len(cells) > 5 else ""

        tenders.append({
            "portal_id": "gil_gujarat",
            "portal_name": "Gujarat Informatics Ltd (GIL)",
            "tender_id": ten_id or sr_no,
            "ref_number": sr_no,
            "title": title[:500],
            "pre_bid_meeting": pre_bid,
            "closing_date": closing_date,
            "opening_date": opening_date,
            "status": "Active",
            "detail_url": f"{GIL_BASE}/TenderDetails.aspx?TenID={ten_id}" if ten_id else "",
            "scraped_at": datetime.now().isoformat(),
            "page_num": str(page_num),
            "detail_scraped": "False",
            "location": "Gujarat",
        })

    return tenders


def _gil_fetch_detail(session: requests.Session, ten_id: str) -> dict | None:
    """Fetch detail page for a single tender from gil.gujarat.gov.in."""
    try:
        url = GIL_DETAIL_URL.format(ten_id=ten_id)
        resp = session.get(url, timeout=20)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        detail = {}

        # Extract key-value pairs from detail tables
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                value = cells[1].get_text(strip=True)
                if not value or value == "-" or value == "N/A":
                    continue

                if "organisation" in label or "department" in label or "company" in label:
                    detail["organisation"] = value
                elif "description" in label or "scope" in label or "work" in label:
                    detail["work_description"] = value[:2000]
                elif "estimated" in label or "tender value" in label:
                    detail["tender_value_inr"] = value
                elif "emd" in label or "earnest" in label:
                    detail["emd_inr"] = value
                elif "document fee" in label or "tender fee" in label:
                    detail["tender_fee_inr"] = value
                elif "location" in label or "place" in label:
                    detail["location"] = value
                elif "category" in label or "type" in label:
                    detail["tender_type"] = value
                elif "published" in label or "start date" in label:
                    detail["published_date"] = value
                elif "ref" in label or "number" in label:
                    detail["ref_number"] = value
                elif "contact" in label:
                    detail["contact"] = value

        # Look for document download links
        doc_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(ext in href.lower() for ext in [".pdf", ".doc", ".xls", "download", "document"]):
                doc_links.append(urljoin(GIL_BASE, href))
        if doc_links:
            detail["documents"] = " | ".join(doc_links[:10])

        time.sleep(0.3)
        return detail

    except Exception as e:
        log.warning(f"[gil] Detail error id={ten_id}: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def run(args):
    start = time.time()
    all_tenders = []

    if args.source in ("gt", "both"):
        gt_tenders = scrape_gujarattenders(
            max_pages=args.max_pages,
            fetch_details=not args.skip_details,
        )
        # Classify
        for t in gt_tenders:
            text = " ".join(filter(None, [t.get("title", ""), t.get("organisation", "")]))
            t["industry_category"] = classify_tender_local(text)
            t["scrape_date"] = datetime.now().strftime("%Y-%m-%d")
            t["source_portal_type"] = "aggregator"
        all_tenders.extend(gt_tenders)

        if gt_tenders:
            save_csv(gt_tenders, "gujarattenders", OUTPUT_DIR)
            save_json(gt_tenders, "gujarattenders", OUTPUT_DIR)
            save_sqlite(gt_tenders, OUTPUT_DIR)
            save_daily_snapshot(gt_tenders, "gujarattenders")
            log.info(f"[gt] Saved {len(gt_tenders)} tenders")

    if args.source in ("gil", "both"):
        gil_tenders = scrape_gil_gujarat(
            max_pages=args.max_pages,
            fetch_details=not args.skip_details,
        )
        for t in gil_tenders:
            text = " ".join(filter(None, [t.get("title", ""), t.get("organisation", "")]))
            t["industry_category"] = classify_tender_local(text)
            t["scrape_date"] = datetime.now().strftime("%Y-%m-%d")
            t["source_portal_type"] = "government"
        all_tenders.extend(gil_tenders)

        if gil_tenders:
            save_csv(gil_tenders, "gil_gujarat", OUTPUT_DIR)
            save_json(gil_tenders, "gil_gujarat", OUTPUT_DIR)
            save_sqlite(gil_tenders, OUTPUT_DIR)
            save_daily_snapshot(gil_tenders, "gil_gujarat")
            log.info(f"[gil] Saved {len(gil_tenders)} tenders")

    elapsed = time.time() - start
    log.info(f"\n{'='*60}")
    log.info(f"GUJARAT SCRAPE COMPLETE")
    log.info(f"Total: {len(all_tenders)} tenders in {elapsed:.0f}s")
    log.info(f"{'='*60}")
    return all_tenders


def main():
    parser = argparse.ArgumentParser(description="Scrape Gujarat tender portals")
    parser.add_argument("--source", choices=["gt", "gil", "both"], default="both",
                        help="Which source: gt=gujarattenders.in, gil=gil.gujarat.gov.in, both (default)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Max pages per source (default: all)")
    parser.add_argument("--skip-details", action="store_true",
                        help="Skip detail page fetching (faster)")
    args = parser.parse_args()

    try:
        run(args)
    except KeyboardInterrupt:
        log.info("\nInterrupted.")


if __name__ == "__main__":
    main()
