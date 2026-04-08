#!/usr/bin/env python3
"""
New Portals Scraper — BSNL, NHPC, AP, Telangana, WB, Bihar, Chhattisgarh
═════════════════════════════════════════════════════════════════════════
Scrapes tenders from previously-unreachable portals using requests + BS4,
plus Playwright for session-based NIC/GePNIC portals.

Usage:
    python scrape_new_portals.py                       # all portals
    python scrape_new_portals.py --portals bsnl nhpc   # specific portals
    python scrape_new_portals.py --skip-details        # listings only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
import json
import urllib3
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
        logging.FileHandler("logs/new_portals.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("new_portals")

import requests
from bs4 import BeautifulSoup

from core.storage import save_csv, save_json, save_sqlite, save_daily_snapshot, OUTPUT_DIR
from ai.classifier import classify_tender_local

# Suppress SSL warnings for Bihar
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


# ═════════════════════════════════════════════════════════════════════════════
# 1. BSNL — Simple JSP table with ?mv=N pagination
# ═════════════════════════════════════════════════════════════════════════════

BSNL_URL = "https://tender.bsnl.co.in/bsnltenders/bsnltender/tender_live_view_main.jsp"

def scrape_bsnl(fetch_details: bool) -> list[dict]:
    log.info("═══ BSNL Tenders ═══")
    session = requests.Session()
    session.headers.update(HEADERS)

    all_tenders = []
    page = 1

    while True:
        url = f"{BSNL_URL}?mv={page}"
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            log.error(f"[bsnl] Page {page} failed: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")

        # Find the main data table by id or structure
        tenders_on_page = []
        table = soup.find("table", id="table1")
        if not table:
            # Fallback: find table with 10+ column rows
            for t in tables:
                if any(len(r.find_all("td")) >= 8 for r in t.find_all("tr")):
                    table = t
                    break

        if table:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 10:
                    continue
                # Skip header row
                if cells[0].get_text(strip=True) == "SNo":
                    continue

                sr_no = cells[0].get_text(strip=True)
                if not sr_no or not sr_no[0].isdigit():
                    continue

                circle = cells[1].get_text(strip=True)
                ssa_unit = cells[2].get_text(strip=True)
                category = cells[3].get_text(strip=True)
                department = cells[4].get_text(strip=True)
                date_of_issue = cells[5].get_text(strip=True).split("\x00")[0].strip()
                submission_date = cells[6].get_text(strip=True).split("\x00")[0].strip()
                open_date = cells[7].get_text(strip=True).split("\x00")[0].strip()
                tender_number = cells[8].get_text(strip=True)
                title_text = cells[9].get_text(strip=True)

                # Detail link
                detail_url = ""
                link = row.find("a", href=True)
                tid_from_link = ""
                if link:
                    href = link["href"]
                    detail_url = urljoin("https://tender.bsnl.co.in/bsnltenders/bsnltender/", href)
                    tid_match = re.search(r"tenderID=(\d+)", href)
                    if tid_match:
                        tid_from_link = tid_match.group(1)

                tender_id = tid_from_link or tender_number or f"BSNL-{sr_no}-{page}"
                tenders_on_page.append({
                    "portal_id": "bsnl",
                    "portal_name": "BSNL Tenders",
                    "tender_id": tender_id,
                    "title": title_text[:500] or f"{department} - {category} ({circle}/{ssa_unit})",
                    "ref_number": tender_number,
                    "organisation": f"BSNL {circle}",
                    "department": department,
                    "location": circle,
                    "published_date": date_of_issue,
                    "closing_date": submission_date,
                    "opening_date": open_date,
                    "tender_type": category,
                    "status": "Active",
                    "detail_url": detail_url,
                    "scraped_at": datetime.now().isoformat(),
                    "detail_scraped": "False",
                })

        if not tenders_on_page:
            log.info(f"[bsnl] Page {page}: no tenders, stopping.")
            break

        all_tenders.extend(tenders_on_page)
        log.info(f"[bsnl] Page {page}: {len(tenders_on_page)} tenders (total: {len(all_tenders)})")

        # Check if there's a next page
        next_link = soup.find("a", string=re.compile(r"Next|>>|›", re.I))
        if not next_link:
            # Also check by page number
            page_links = soup.find_all("a", href=lambda h: h and f"mv={page+1}" in h)
            if not page_links:
                log.info(f"[bsnl] No next page after {page}, done.")
                break

        page += 1
        time.sleep(0.5)

    # Fetch details if requested
    if fetch_details and all_tenders:
        _bsnl_fetch_details(session, all_tenders)

    log.info(f"[bsnl] Done: {len(all_tenders)} tenders from {page} pages")
    return all_tenders


def _bsnl_fetch_details(session, tenders):
    log.info(f"[bsnl] Fetching details for {len(tenders)} tenders...")
    for i, t in enumerate(tenders):
        url = t.get("detail_url", "")
        if not url:
            continue
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            # Extract any additional info from detail page
            text_content = soup.get_text(" ", strip=True)[:3000]
            if text_content:
                t["work_description"] = text_content[:2000]
                t["detail_scraped"] = "True"
            if (i + 1) % 50 == 0:
                log.info(f"[bsnl] Details: {i+1}/{len(tenders)}")
        except Exception as e:
            log.warning(f"[bsnl] Detail error: {e}")
        time.sleep(0.3)


# ═════════════════════════════════════════════════════════════════════════════
# 2. NHPC — nhpcindia.com (static HTML) + tenderdetail.com (AJAX API)
# ═════════════════════════════════════════════════════════════════════════════

NHPC_URL = "https://www.nhpcindia.com/welcome/tender"
NHPC_TD_URL = "https://www.tenderdetail.com/government-tenders/national-hydroelectric-power-corporation-limited-tenders/1"

def scrape_nhpc(fetch_details: bool) -> list[dict]:
    log.info("═══ NHPC Tenders ═══")
    all_tenders = []

    # Source 1: nhpcindia.com
    nhpc_tenders = _scrape_nhpc_official()
    all_tenders.extend(nhpc_tenders)

    # Source 2: tenderdetail.com NHPC
    td_tenders = _scrape_nhpc_tenderdetail()
    all_tenders.extend(td_tenders)

    log.info(f"[nhpc] Done: {len(all_tenders)} total ({len(nhpc_tenders)} official + {len(td_tenders)} tenderdetail)")
    return all_tenders


def _scrape_nhpc_official() -> list[dict]:
    log.info("[nhpc] Scraping nhpcindia.com...")
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        resp = session.get(NHPC_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"[nhpc] Official site failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    tenders = []

    # Find tender blocks — look for divs with "Tender Title" or similar patterns
    # Try table rows first
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                text = row.get_text(" ", strip=True)
                if "tender" in text.lower() or "nit" in text.lower():
                    # This might be a tender row
                    pass

    # Try div-based layout
    tender_blocks = soup.find_all("div", class_=re.compile(r"tender|card|item|list", re.I))
    if not tender_blocks:
        # Try finding by strong tags with "Tender Title"
        strong_tags = soup.find_all("strong", string=re.compile(r"Tender Title", re.I))
        for tag in strong_tags:
            block = tag.find_parent("div") or tag.find_parent("tr")
            if block:
                tender_blocks.append(block)

    # Find all links to tender_detail pages
    detail_links = soup.find_all("a", href=re.compile(r"tender_detail|tender-detail", re.I))
    seen_ids = set()

    for link in detail_links:
        href = link.get("href", "")
        tid_match = re.search(r"/(\d+)/?$", href)
        tid = tid_match.group(1) if tid_match else ""
        if not tid or tid in seen_ids:
            continue
        seen_ids.add(tid)

        link_text = link.get_text(strip=True)
        # Walk up to parent block for full tender info
        parent = link.find_parent("div") or link.find_parent("tr") or link.find_parent("li")
        title = ""
        nit_no = ""
        location = ""
        if parent:
            # Get all text in the parent block
            full_text = parent.get_text(" ", strip=True)
            # Title: extract from strong tags or first meaningful text
            strong = parent.find("strong", string=re.compile(r"Tender Title", re.I))
            if strong:
                # Text after "Tender Title :"
                title_match = re.search(r"Tender\s+Title\s*:?\s*(.+?)(?:NIT|Location|$)", full_text, re.I)
                if title_match:
                    title = title_match.group(1).strip()
            if not title:
                # Use first substantial text that isn't "View More"
                for child in parent.children:
                    t = child.string or (child.get_text(strip=True) if hasattr(child, 'get_text') else "")
                    t = t.strip()
                    if t and len(t) > 10 and t.lower() != "view more":
                        title = t
                        break
            if not title:
                # Fallback: full block text minus "View More"
                title = re.sub(r"\s*View More\s*", "", full_text).strip()[:500]

            nit_match = re.search(r"NIT\s*(?:No\.?|Number)?\s*:?\s*([^\n|]+)", full_text, re.I)
            if nit_match:
                nit_no = nit_match.group(1).strip()
            loc_match = re.search(r"Location\s*:?\s*([^\n|]+)", full_text, re.I)
            if loc_match:
                location = loc_match.group(1).strip()

        if not title or title.lower() == "view more":
            title = link_text if link_text.lower() != "view more" else f"NHPC Tender {tid}"

        tenders.append({
            "portal_id": "nhpc",
            "portal_name": "NHPC India",
            "tender_id": f"NHPC-{tid}",
            "title": title[:500],
            "ref_number": nit_no,
            "organisation": "NHPC Limited",
            "location": location,
            "status": "Active",
            "detail_url": urljoin("https://www.nhpcindia.com", href),
            "scraped_at": datetime.now().isoformat(),
            "detail_scraped": "False",
        })

    log.info(f"[nhpc] Official: {len(tenders)} tenders")
    return tenders


def _scrape_nhpc_tenderdetail() -> list[dict]:
    """Scrape NHPC tenders from tenderdetail.com using AJAX API."""
    log.info("[nhpc] Scraping tenderdetail.com (NHPC)...")
    session = requests.Session()
    session.headers.update(HEADERS)

    tenders = []
    page = 1

    while True:
        try:
            # First load the page to get any cookies/tokens
            url = f"https://www.tenderdetail.com/government-tenders/national-hydroelectric-power-corporation-limited-tenders/{page}?agid=10197"
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                log.warning(f"[nhpc-td] Page {page} returned {resp.status_code}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")

            # Parse listing items
            items = soup.find_all("div", class_=re.compile(r"listing|tender|item", re.I))
            if not items:
                # Try numbered list items or li tags
                items = soup.find_all("li", class_=re.compile(r"listing|tender", re.I))
            if not items:
                # Try all links to TenderNotice
                items = soup.find_all("a", href=re.compile(r"/TenderNotice/", re.I))

            page_tenders = []
            seen_ids = set()

            for item in items:
                if isinstance(item, type(soup.new_tag("a"))) and item.name == "a":
                    link = item
                    parent = item.find_parent("div") or item.find_parent("li")
                else:
                    link = item.find("a", href=re.compile(r"/TenderNotice/", re.I))
                    parent = item

                if not link:
                    continue

                href = link.get("href", "")
                tid_match = re.search(r"/TenderNotice/(\d+)", href)
                tid = tid_match.group(1) if tid_match else ""
                if not tid or tid in seen_ids:
                    continue
                seen_ids.add(tid)

                title = link.get_text(strip=True)
                due_date = ""
                value = ""
                location = ""

                if parent:
                    text = parent.get_text(" ", strip=True)
                    due_match = re.search(r"Due\s*Date\s*:?\s*([\d\-\/\s\w]+?)(?:\s{2}|\||$)", text)
                    if due_match:
                        due_date = due_match.group(1).strip()
                    val_match = re.search(r"₹\s*([\d,\.]+\s*(?:Lakh|Crore|Cr)?)", text, re.I)
                    if val_match:
                        value = val_match.group(1).strip()
                    loc_match = re.search(r"Location\s*:?\s*([^\n|]+)", text, re.I)
                    if loc_match:
                        location = loc_match.group(1).strip()

                page_tenders.append({
                    "portal_id": "nhpc_td",
                    "portal_name": "NHPC (via TenderDetail)",
                    "tender_id": f"TD-NHPC-{tid}",
                    "title": title[:500],
                    "organisation": "NHPC Limited",
                    "closing_date": due_date,
                    "tender_value_inr": value,
                    "location": location,
                    "status": "Active",
                    "detail_url": urljoin("https://www.tenderdetail.com", href),
                    "scraped_at": datetime.now().isoformat(),
                    "detail_scraped": "False",
                })

            if not page_tenders:
                log.info(f"[nhpc-td] Page {page}: no tenders, stopping.")
                break

            tenders.extend(page_tenders)
            log.info(f"[nhpc-td] Page {page}: {len(page_tenders)} tenders (total: {len(tenders)})")

            # Check for next page link
            next_page = soup.find("a", href=re.compile(rf"/{page+1}\?"))
            if not next_page:
                next_page = soup.find("a", string=re.compile(r"Next|»|›", re.I))
            if not next_page:
                break

            page += 1
            time.sleep(0.5)

        except Exception as e:
            log.error(f"[nhpc-td] Page {page} error: {e}")
            break

    log.info(f"[nhpc-td] Done: {len(tenders)} tenders from {page} pages")
    return tenders


# ═════════════════════════════════════════════════════════════════════════════
# 3. West Bengal — NIC GePNIC at tenders.wb.gov.in
# ═════════════════════════════════════════════════════════════════════════════

WB_URL = "https://tenders.wb.gov.in/nicgep/app"

def scrape_west_bengal(fetch_details: bool) -> list[dict]:
    """Scrape West Bengal tenders using Playwright (NIC GePNIC needs JS)."""
    log.info("═══ West Bengal Tenders ═══")
    return asyncio.get_event_loop().run_until_complete(_scrape_wb_async())


async def _scrape_wb_async() -> list[dict]:
    from core.browser import BrowserSession
    tenders = []

    async with BrowserSession(headless=True) as session:
        page = await session.new_page()
        try:
            await page.goto(WB_URL, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            log.error(f"[wb] Failed to load: {e}")
            return []

        # Get latest tenders table
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")

        # Find tender tables
        for table in soup.find_all("table"):
            header_text = table.get_text(" ", strip=True).lower()
            if "tender" not in header_text:
                continue

            rows = table.find_all("tr")
            for row in rows[1:]:  # skip header
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                title_cell = cells[0] if cells else None
                title = title_cell.get_text(strip=True) if title_cell else ""
                if not title or len(title) < 10:
                    continue

                # Extract link
                link = row.find("a", href=True)
                detail_url = ""
                tid = ""
                if link:
                    href = link.get("href", "")
                    detail_url = urljoin(WB_URL, href)
                    tid_match = re.search(r"(\d{5,})", href)
                    if tid_match:
                        tid = tid_match.group(1)

                ref_no = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                closing = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                opening = cells[3].get_text(strip=True) if len(cells) > 3 else ""

                tenders.append({
                    "portal_id": "west_bengal",
                    "portal_name": "West Bengal eProcurement",
                    "tender_id": tid or f"WB-{hash(title) % 100000}",
                    "title": title[:500],
                    "ref_number": ref_no,
                    "closing_date": closing,
                    "opening_date": opening,
                    "organisation": "Govt of West Bengal",
                    "location": "West Bengal",
                    "status": "Active",
                    "detail_url": detail_url,
                    "scraped_at": datetime.now().isoformat(),
                    "detail_scraped": "False",
                })

        # Try "More..." links for additional tenders
        more_links = await page.query_selector_all("a:has-text('More')")
        for more_link in more_links:
            try:
                await more_link.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                content = await page.content()
                soup2 = BeautifulSoup(content, "html.parser")
                for table in soup2.find_all("table"):
                    rows = table.find_all("tr")
                    for row in rows[1:]:
                        cells = row.find_all("td")
                        if len(cells) < 3:
                            continue
                        title = cells[0].get_text(strip=True)
                        if not title or len(title) < 10:
                            continue
                        link = row.find("a", href=True)
                        detail_url = ""
                        tid = ""
                        if link:
                            href = link.get("href", "")
                            detail_url = urljoin(WB_URL, href)
                            tid_match = re.search(r"(\d{5,})", href)
                            if tid_match:
                                tid = tid_match.group(1)
                        ref_no = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                        closing = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                        existing_ids = {t["tender_id"] for t in tenders}
                        new_tid = tid or f"WB-{hash(title) % 100000}"
                        if new_tid not in existing_ids:
                            tenders.append({
                                "portal_id": "west_bengal",
                                "portal_name": "West Bengal eProcurement",
                                "tender_id": new_tid,
                                "title": title[:500],
                                "ref_number": ref_no,
                                "closing_date": closing,
                                "organisation": "Govt of West Bengal",
                                "location": "West Bengal",
                                "status": "Active",
                                "detail_url": detail_url,
                                "scraped_at": datetime.now().isoformat(),
                                "detail_scraped": "False",
                            })
            except Exception as e:
                log.warning(f"[wb] More link failed: {e}")

        await page.close()

    log.info(f"[wb] Done: {len(tenders)} tenders")
    return tenders


# ═════════════════════════════════════════════════════════════════════════════
# 4. Bihar — eproc2.bihar.gov.in (Struts2, SSL verify=False)
# ═════════════════════════════════════════════════════════════════════════════

BIHAR_URL = "https://eproc2.bihar.gov.in/EPSV2Web/openarea/tenderListingPage.action"

def scrape_bihar(fetch_details: bool) -> list[dict]:
    """Scrape Bihar tenders using Playwright (handles SSL + JS)."""
    log.info("═══ Bihar Tenders ═══")
    return asyncio.get_event_loop().run_until_complete(_scrape_bihar_async())


async def _scrape_bihar_async() -> list[dict]:
    from core.browser import BrowserSession
    tenders = []

    async with BrowserSession(headless=True) as session:
        page = await session.new_page()
        try:
            await page.goto(BIHAR_URL, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
        except Exception as e:
            log.error(f"[bihar] Failed to load: {e}")
            return []

        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")

        # Find tender listing table
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue

                # Try to extract tender info from cells
                texts = [c.get_text(strip=True) for c in cells]
                # Skip empty rows
                if not any(t for t in texts if len(t) > 5):
                    continue

                title = ""
                ref_no = ""
                closing = ""
                org = ""
                tid = ""

                link = row.find("a", href=True)
                if link:
                    title = link.get_text(strip=True)
                    href = link.get("href", "")
                    tid_match = re.search(r"tenderId=(\d+)", href)
                    if tid_match:
                        tid = tid_match.group(1)

                # Parse cells based on position
                for text in texts:
                    if re.match(r"\d{2}[/-]\d{2}[/-]\d{4}", text):
                        closing = text
                    elif re.match(r"[A-Z]{2,}[/-]\d+", text):
                        ref_no = text

                if not title:
                    title = " | ".join(t for t in texts if len(t) > 10)[:500]
                if not title:
                    continue

                tenders.append({
                    "portal_id": "bihar",
                    "portal_name": "Bihar eProcurement",
                    "tender_id": tid or f"BH-{hash(title) % 100000}",
                    "title": title[:500],
                    "ref_number": ref_no,
                    "closing_date": closing,
                    "organisation": org or "Govt of Bihar",
                    "location": "Bihar",
                    "status": "Active",
                    "scraped_at": datetime.now().isoformat(),
                    "detail_scraped": "False",
                })

        # Try clicking "Latest Tenders" tab if exists
        try:
            latest_tab = await page.query_selector("a:has-text('Latest Tenders'), a:has-text('Active Tenders')")
            if latest_tab:
                await latest_tab.click()
                await page.wait_for_timeout(3000)
                content2 = await page.content()
                soup2 = BeautifulSoup(content2, "html.parser")
                existing_ids = {t["tender_id"] for t in tenders}
                for table in soup2.find_all("table"):
                    for row in table.find_all("tr")[1:]:
                        cells = row.find_all("td")
                        if len(cells) < 3:
                            continue
                        link = row.find("a", href=True)
                        if link:
                            title = link.get_text(strip=True)
                            href = link.get("href", "")
                            tid_match = re.search(r"tenderId=(\d+)", href)
                            tid = tid_match.group(1) if tid_match else f"BH-{hash(title) % 100000}"
                            if tid not in existing_ids:
                                tenders.append({
                                    "portal_id": "bihar",
                                    "portal_name": "Bihar eProcurement",
                                    "tender_id": tid,
                                    "title": title[:500],
                                    "organisation": "Govt of Bihar",
                                    "location": "Bihar",
                                    "status": "Active",
                                    "scraped_at": datetime.now().isoformat(),
                                    "detail_scraped": "False",
                                })
        except Exception as e:
            log.warning(f"[bihar] Tab click failed: {e}")

        await page.close()

    log.info(f"[bihar] Done: {len(tenders)} tenders")
    return tenders


# ═════════════════════════════════════════════════════════════════════════════
# 5. Chhattisgarh — Public listing table with CSRF
# ═════════════════════════════════════════════════════════════════════════════

CG_URL = "https://eproc.cgstate.gov.in/CHEPS/security/getSignInAction.do"

def scrape_chhattisgarh(fetch_details: bool) -> list[dict]:
    """Scrape Chhattisgarh tenders using Playwright."""
    log.info("═══ Chhattisgarh Tenders ═══")
    return asyncio.get_event_loop().run_until_complete(_scrape_cg_async())


async def _scrape_cg_async() -> list[dict]:
    from core.browser import BrowserSession
    tenders = []

    async with BrowserSession(headless=True) as session:
        page = await session.new_page()
        try:
            await page.goto(CG_URL, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            log.error(f"[cg] Failed to load: {e}")
            return []

        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")

        # Find open tender table
        for table in soup.find_all("table"):
            header = table.get_text(" ", strip=True)[:200].lower()
            if "tender" not in header and "auction" not in header:
                continue

            rows = table.find_all("tr")
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                texts = [c.get_text(strip=True) for c in cells]
                if not any(t for t in texts if len(t) > 5):
                    continue

                link = row.find("a", href=True)
                title = ""
                detail_url = ""
                tid = ""
                if link:
                    title = link.get_text(strip=True)
                    href = link.get("href", "")
                    detail_url = urljoin("https://eproc.cgstate.gov.in", href)
                    tid_match = re.search(r"tenderId=(\w+)", href)
                    if tid_match:
                        tid = tid_match.group(1)

                tender_no = ""
                closing = ""
                value = ""
                for text in texts:
                    if re.match(r"\d{2}[/-]\d{2}[/-]\d{4}", text):
                        if not closing:
                            closing = text
                    elif re.search(r"CG|CHEPS|/\d{4}", text) and not tender_no:
                        tender_no = text

                if not title:
                    title = " | ".join(t for t in texts if len(t) > 10)[:500]
                if not title:
                    continue

                tenders.append({
                    "portal_id": "chhattisgarh",
                    "portal_name": "Chhattisgarh eProcurement",
                    "tender_id": tid or tender_no or f"CG-{hash(title) % 100000}",
                    "title": title[:500],
                    "ref_number": tender_no,
                    "closing_date": closing,
                    "organisation": "Govt of Chhattisgarh",
                    "location": "Chhattisgarh",
                    "status": "Active",
                    "detail_url": detail_url,
                    "scraped_at": datetime.now().isoformat(),
                    "detail_scraped": "False",
                })

        await page.close()

    log.info(f"[cg] Done: {len(tenders)} tenders")
    return tenders


# ═════════════════════════════════════════════════════════════════════════════
# 6. Andhra Pradesh — GePNIC variant (session-based)
# ═════════════════════════════════════════════════════════════════════════════

AP_URL = "https://tender.apeprocurement.gov.in"

def scrape_andhra_pradesh(fetch_details: bool) -> list[dict]:
    log.info("═══ Andhra Pradesh Tenders ═══")
    return asyncio.get_event_loop().run_until_complete(_scrape_ap_ts_async("ap", AP_URL))


# ═════════════════════════════════════════════════════════════════════════════
# 7. Telangana — Same architecture as AP
# ═════════════════════════════════════════════════════════════════════════════

TS_URL = "https://tender.telangana.gov.in"

def scrape_telangana(fetch_details: bool) -> list[dict]:
    log.info("═══ Telangana Tenders ═══")
    return asyncio.get_event_loop().run_until_complete(_scrape_ap_ts_async("ts", TS_URL))


async def _scrape_ap_ts_async(state_code: str, base_url: str) -> list[dict]:
    """Scrape AP/TS eProcurement portals using Playwright.
    These are custom JSP portals that need session + CSRF tokens."""
    from core.browser import BrowserSession
    tenders = []
    state_name = "Andhra Pradesh" if state_code == "ap" else "Telangana"
    portal_id = "andhra_pradesh" if state_code == "ap" else "telangana"

    async with BrowserSession(headless=True) as session:
        page = await session.new_page()

        # Load homepage to establish session
        try:
            await page.goto(f"{base_url}/login.html", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            log.error(f"[{state_code}] Failed to load homepage: {e}")
            return []

        # Try to navigate to tender listing pages
        listing_urls = [
            f"{base_url}/TenderDetailsHome.html",
            f"{base_url}/PublishedTender.html",
            f"{base_url}/ActiveTenderdetailsHome.html",
        ]

        for url in listing_urls:
            try:
                await page.goto(url, timeout=20000)
                await page.wait_for_timeout(3000)
                content = await page.content()

                # Check if redirected to session timeout
                if "SessionTimeOut" in content or "session" in content.lower() and "expired" in content.lower():
                    log.warning(f"[{state_code}] {url} requires login, trying next...")
                    continue

                soup = BeautifulSoup(content, "html.parser")

                # Parse tender tables
                for table in soup.find_all("table"):
                    rows = table.find_all("tr")
                    if len(rows) < 2:
                        continue

                    for row in rows[1:]:
                        cells = row.find_all("td")
                        if len(cells) < 3:
                            continue

                        link = row.find("a", href=True)
                        title = ""
                        detail_url_str = ""
                        tid = ""

                        if link:
                            title = link.get_text(strip=True)
                            href = link.get("href", "")
                            detail_url_str = urljoin(base_url, href)
                            tid_match = re.search(r"(\d{5,})", href)
                            if tid_match:
                                tid = tid_match.group(1)

                        texts = [c.get_text(strip=True) for c in cells]
                        if not title:
                            title = " | ".join(t for t in texts if len(t) > 10)[:500]
                        if not title or len(title) < 5:
                            continue

                        ref_no = ""
                        closing = ""
                        for text in texts:
                            if re.match(r"\d{2}[/-]\d{2}[/-]\d{4}", text):
                                closing = text
                            elif re.search(r"/\d{4}|NIT|RFP", text) and not ref_no:
                                ref_no = text

                        tenders.append({
                            "portal_id": portal_id,
                            "portal_name": f"{state_name} eProcurement",
                            "tender_id": tid or f"{state_code.upper()}-{hash(title) % 100000}",
                            "title": title[:500],
                            "ref_number": ref_no,
                            "closing_date": closing,
                            "organisation": f"Govt of {state_name}",
                            "location": state_name,
                            "status": "Active",
                            "detail_url": detail_url_str,
                            "scraped_at": datetime.now().isoformat(),
                            "detail_scraped": "False",
                        })

                if tenders:
                    break  # Got tenders from this URL

            except Exception as e:
                log.warning(f"[{state_code}] {url} failed: {e}")
                continue

        # Also try scraping from homepage widgets (corrigendum/recent tenders)
        try:
            await page.goto(f"{base_url}/login.html", timeout=20000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")

            # Homepage has hardcoded tender IDs in widgets
            existing_ids = {t["tender_id"] for t in tenders}
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if "tender" in href.lower() or "procurement" in href.lower():
                    tid_match = re.search(r"(\d{6,})", href)
                    if tid_match:
                        tid = tid_match.group(1)
                        if tid not in existing_ids:
                            title = link.get_text(strip=True)
                            if title and len(title) > 5:
                                existing_ids.add(tid)
                                tenders.append({
                                    "portal_id": portal_id,
                                    "portal_name": f"{state_name} eProcurement",
                                    "tender_id": tid,
                                    "title": title[:500],
                                    "organisation": f"Govt of {state_name}",
                                    "location": state_name,
                                    "status": "Active",
                                    "detail_url": urljoin(base_url, href),
                                    "scraped_at": datetime.now().isoformat(),
                                    "detail_scraped": "False",
                                })
        except Exception as e:
            log.warning(f"[{state_code}] Homepage scrape failed: {e}")

        await page.close()

    log.info(f"[{state_code}] Done: {len(tenders)} tenders")
    return tenders


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

ALL_PORTALS = {
    "bsnl": ("BSNL Tenders", scrape_bsnl),
    "nhpc": ("NHPC Tenders", scrape_nhpc),
    "wb": ("West Bengal", scrape_west_bengal),
    "bihar": ("Bihar", scrape_bihar),
    "cg": ("Chhattisgarh", scrape_chhattisgarh),
    "ap": ("Andhra Pradesh", scrape_andhra_pradesh),
    "ts": ("Telangana", scrape_telangana),
}


def main():
    parser = argparse.ArgumentParser(description="Scrape new tender portals")
    parser.add_argument("--portals", nargs="+", choices=list(ALL_PORTALS.keys()),
                        default=list(ALL_PORTALS.keys()),
                        help="Which portals to scrape (default: all)")
    parser.add_argument("--skip-details", action="store_true",
                        help="Skip detail page fetching")
    args = parser.parse_args()

    start = time.time()
    grand_total = []

    for key in args.portals:
        name, scraper = ALL_PORTALS[key]
        log.info(f"\n{'='*60}")
        try:
            tenders = scraper(fetch_details=not args.skip_details)
        except Exception as e:
            log.error(f"[{key}] CRASHED: {e}")
            tenders = []

        if tenders:
            # Classify
            for t in tenders:
                text = " ".join(filter(None, [t.get("title", ""), t.get("organisation", "")]))
                t["industry_category"] = classify_tender_local(text)
                t["scrape_date"] = datetime.now().strftime("%Y-%m-%d")
                t["source_portal_type"] = "government"

            pid = tenders[0]["portal_id"]
            save_csv(tenders, pid, OUTPUT_DIR)
            save_json(tenders, pid, OUTPUT_DIR)
            save_sqlite(tenders, OUTPUT_DIR)
            save_daily_snapshot(tenders, pid)
            log.info(f"[{key}] Saved {len(tenders)} tenders")
            grand_total.extend(tenders)
        else:
            log.warning(f"[{key}] No tenders collected")

    elapsed = time.time() - start
    log.info(f"\n{'='*60}")
    log.info(f"NEW PORTALS SCRAPE COMPLETE")
    log.info(f"Total: {len(grand_total)} tenders in {elapsed:.0f}s")
    for key in args.portals:
        count = sum(1 for t in grand_total if t.get("portal_id") == ALL_PORTALS[key][1].__name__.replace("scrape_", ""))
        # Just count by portal key
        pass
    # Summary by portal_id
    from collections import Counter
    counts = Counter(t["portal_id"] for t in grand_total)
    for pid, cnt in counts.most_common():
        log.info(f"  {pid:<25} {cnt:>6}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
