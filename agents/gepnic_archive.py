"""
GePNIC Archive + Awards Scraper.

Scrapes:
  - Archive / past / closed tenders
  - Awarded contracts (AOC - Award of Contract)

Works for ALL 14 GePNIC-based portals:
  DefProc, NHAI, BSNL, NTPC, Coal India, Maharashtra,
  UP, Tamil Nadu, Rajasthan, Gujarat, MES, ONGC, etc.

Strategy:
  1. Try DirectLink API endpoint first (fastest, no CAPTCHA)
  2. Fall back to form-based search with AI CAPTCHA solving
  3. Fall back to paginated archive page scraping
  4. Cookie warmup + session reuse to avoid blocks
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from playwright.async_api import Page

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay, wait_for_content, human_scroll
from ai.captcha_advanced import solve_any_captcha, warmup_session_cookies

log = logging.getLogger("agents.gepnic_archive")


# ── Date helpers ──────────────────────────────────────────────────────────────

def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y")


def _date_ranges(scope: str) -> list[tuple[str, str]]:
    """
    Return list of (from_date, to_date) strings to query.
    Splits into 6-month chunks to avoid portal result limits.
    """
    today = datetime.today()

    if scope == "archive":
        # Last 5 years in 6-month chunks
        ranges = []
        end = today
        for _ in range(10):
            start = end - timedelta(days=180)
            ranges.append((_fmt_date(start), _fmt_date(end)))
            end = start - timedelta(days=1)
        return ranges

    elif scope == "awards":
        # Last 3 years in 6-month chunks
        ranges = []
        end = today
        for _ in range(6):
            start = end - timedelta(days=180)
            ranges.append((_fmt_date(start), _fmt_date(end)))
            end = start - timedelta(days=1)
        return ranges

    elif scope == "both":
        return _date_ranges("archive") + _date_ranges("awards")

    else:
        # Single range — last 6 months
        return [(_fmt_date(today - timedelta(days=180)), _fmt_date(today))]


# ── Row parsers ───────────────────────────────────────────────────────────────

def _parse_gepnic_row(cells: list[str], portal_id: str, portal_name: str, status: str) -> dict:
    """Parse a GePNIC table row into a tender dict."""
    # GePNIC columns vary by portal but generally:
    # [SNo, TenderID/RefNo, Title, Org, PublishedDate, ClosingDate, Value, ...]
    def safe(i: int) -> str:
        return cells[i].strip() if i < len(cells) else ""

    return {
        "portal_id":        portal_id,
        "portal_name":      portal_name,
        "tender_id":        safe(1) or safe(2),
        "ref_number":       safe(2),
        "title":            safe(3) or safe(2),
        "organisation":     safe(4) or safe(3),
        "published_date":   safe(5) or safe(4),
        "closing_date":     safe(6) or safe(5),
        "opening_date":     safe(7) or "",
        "tender_value_inr": safe(8) or "",
        "emd_inr":          safe(9) or "",
        "status":           status,
        "scraped_at":       datetime.utcnow().isoformat(),
    }


def _parse_award_row(cells: list[str], portal_id: str, portal_name: str) -> dict:
    """Parse an awards/AOC table row."""
    def safe(i: int) -> str:
        return cells[i].strip() if i < len(cells) else ""

    return {
        "portal_id":        portal_id,
        "portal_name":      portal_name,
        "tender_id":        safe(1),
        "ref_number":       safe(2),
        "title":            safe(3),
        "organisation":     safe(4),
        "published_date":   safe(5),
        "closing_date":     safe(6),
        "tender_value_inr": safe(7) or "",
        "award_winner":     safe(8) or safe(7),
        "award_date":       safe(9) or safe(8),
        "award_amount":     safe(10) or safe(9),
        "aoc_no":           safe(11) or safe(10),
        "status":           "Awarded",
        "scraped_at":       datetime.utcnow().isoformat(),
    }


# ── Main agent ────────────────────────────────────────────────────────────────

class GePNICArchiveAgent(BaseAgent):
    """
    Scrapes archive + awards data from GePNIC portals.
    Uses 3 strategies in order: DirectLink API → Form search → Page scrape.
    """

    def __init__(self, cfg, session: BrowserSession, scope: str = "archive"):
        super().__init__(cfg)
        self.session = session
        self.scope = scope  # "archive", "awards", or "both"

    async def scrape(
        self,
        max_pages: Optional[int] = None,
        org_filter: Optional[str] = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.config.portal_id)
        all_tenders: list[dict] = []

        scopes_to_run = ["archive", "awards"] if self.scope == "both" else [self.scope]

        for scope in scopes_to_run:
            log.info(f"[gepnic-archive] {self.config.portal_id} — scope={scope}")

            # Strategy 1: DirectLink API (no CAPTCHA, fastest)
            tenders = await self._try_directlink(scope, max_pages, progress_cb)

            # Strategy 2: Form-based search with CAPTCHA solving
            if not tenders:
                tenders = await self._try_form_search(scope, max_pages, org_filter, progress_cb)

            # Strategy 3: Direct page scrape
            if not tenders:
                tenders = await self._try_page_scrape(scope, max_pages, progress_cb)

            if tenders:
                log.info(f"[gepnic-archive] {self.config.portal_id} {scope}: {len(tenders)} tenders")
                all_tenders.extend(tenders)
            else:
                log.warning(f"[gepnic-archive] {self.config.portal_id} {scope}: no data (portal may be blocking)")
                result.errors.append(f"{scope}: no data retrieved")

        result.tenders = all_tenders
        result.pages   = len(all_tenders) // 20 + 1
        return result

    # ── Strategy 1: DirectLink API ────────────────────────────────────────────

    async def _try_directlink(self, scope: str, max_pages, progress_cb) -> list[dict]:
        """
        Many GePNIC portals expose a DirectLink that returns JSON data
        without requiring CAPTCHA. Try it first.
        """
        cfg = self.config
        base = cfg.archive_url if scope == "archive" else cfg.awards_url
        if not base:
            return []

        # Some portals use a different param for archive vs awards
        tenders = []

        try:
            ctx  = await self.session.new_context(portal_id=cfg.portal_id)
            page = await self.session.new_page(ctx, portal_id=cfg.portal_id)

            # Warmup — visit homepage first to get session cookies
            await warmup_session_cookies(page, cfg.base_url)

            # Intercept JSON API responses
            api_data: list[dict] = []

            async def capture_api(response):
                if response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct or "javascript" in ct:
                        try:
                            body = await response.json()
                            if isinstance(body, list) and body:
                                api_data.extend(body)
                            elif isinstance(body, dict):
                                for key in ("data", "tenders", "results", "items", "records"):
                                    if key in body and isinstance(body[key], list):
                                        api_data.extend(body[key])
                                        break
                        except Exception:
                            pass

            page.on("response", capture_api)

            # Navigate to archive/awards URL
            await page.goto(base, wait_until="domcontentloaded", timeout=45_000)
            await wait_for_content(page)
            await asyncio.sleep(2)

            # If we got API data, parse it
            if api_data:
                for item in api_data:
                    t = self._normalise_api_item(item, scope)
                    if t:
                        tenders.append(t)
                log.info(f"[directlink] Got {len(tenders)} tenders via API interception")
            else:
                # Try scraping the page table directly
                tenders = await self._scrape_table(page, scope, max_pages, progress_cb)

            await self.session.save_cookies(ctx, cfg.portal_id)
            await ctx.close()

        except Exception as e:
            log.warning(f"[directlink] {cfg.portal_id}: {e}")

        return tenders

    def _normalise_api_item(self, item: dict, scope: str) -> Optional[dict]:
        """Normalise an API response item to our tender schema."""
        if not item:
            return None

        def g(*keys):
            for k in keys:
                v = item.get(k) or item.get(k.lower()) or item.get(k.upper())
                if v:
                    return str(v).strip()
            return ""

        status = "Awarded" if scope == "awards" else "Archive"

        return {
            "portal_id":      self.config.portal_id,
            "portal_name":    self.config.display_name,
            "tender_id":      g("tenderId", "tender_id", "TenderId", "id"),
            "ref_number":     g("refNo", "ref_number", "RefNo", "referenceNo"),
            "title":          g("tenderTitle", "title", "Title", "subject", "work"),
            "organisation":   g("organisation", "org", "dept", "department", "Organisation"),
            "published_date": g("publishedDate", "published_date", "PublishedDate", "startDate"),
            "closing_date":   g("closingDate", "closing_date", "ClosingDate", "bidDueDate"),
            "tender_value_inr": g("tenderValue", "value", "estimatedValue", "amount"),
            "award_winner":   g("awardedTo", "winner", "vendor", "contractor") if scope == "awards" else "",
            "award_date":     g("awardDate", "aocDate") if scope == "awards" else "",
            "award_amount":   g("awardAmount", "contractValue") if scope == "awards" else "",
            "detail_url":     g("detailUrl", "url", "link", "detailLink"),
            "status":         status,
            "scraped_at":     datetime.utcnow().isoformat(),
        }

    # ── Strategy 2: Form-based search ─────────────────────────────────────────

    async def _try_form_search(self, scope: str, max_pages, org_filter, progress_cb) -> list[dict]:
        """
        Submit the archive search form with date ranges.
        Uses AI CAPTCHA solver when needed.
        """
        cfg   = self.config
        url   = cfg.archive_url if scope == "archive" else cfg.awards_url
        if not url:
            return []

        tenders = []
        date_ranges = _date_ranges(scope)
        if max_pages:
            date_ranges = date_ranges[:max_pages]

        try:
            ctx  = await self.session.new_context(portal_id=cfg.portal_id)
            page = await self.session.new_page(ctx, portal_id=cfg.portal_id)

            await warmup_session_cookies(page, cfg.base_url)

            for from_date, to_date in date_ranges:
                log.info(f"[form-search] {cfg.portal_id} {scope} {from_date}→{to_date}")

                batch = await self._search_date_range(page, url, from_date, to_date, scope, org_filter)
                tenders.extend(batch)

                if progress_cb and batch:
                    await progress_cb(len(date_ranges), len(tenders))

                await random_delay(2.0, 4.0)

            await self.session.save_cookies(ctx, cfg.portal_id)
            await ctx.close()

        except Exception as e:
            log.warning(f"[form-search] {cfg.portal_id}: {e}")

        return tenders

    async def _search_date_range(
        self, page: Page, url: str, from_date: str, to_date: str,
        scope: str, org_filter: Optional[str]
    ) -> list[dict]:
        """Search one date range and return results."""
        tenders = []

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # Solve CAPTCHA if present
            await solve_any_captcha(page, max_total_attempts=3)

            # Fill date fields — try multiple selector patterns
            date_from_sels = [
                "#publishedFrom", "#fromDate", "[name='fromDate']",
                "[name='publishedFrom']", "#tenderDateFrom", "[id*='from' i][type='text']",
                "[placeholder*='from' i]", "[placeholder*='dd/mm' i]",
            ]
            date_to_sels = [
                "#publishedTo", "#toDate", "[name='toDate']",
                "[name='publishedTo']", "#tenderDateTo", "[id*='to' i][type='text']",
                "[placeholder*='to' i]",
            ]

            await self._fill_field(page, date_from_sels, from_date)
            await self._fill_field(page, date_to_sels,   to_date)

            # Fill org filter if provided
            if org_filter:
                org_sels = [
                    "[name*='org' i]", "[name*='dept' i]", "[name*='organisation' i]",
                    "#organisation", "#orgName",
                ]
                await self._fill_field(page, org_sels, org_filter)

            # Submit form
            submit_sels = [
                "input[type='submit']", "button[type='submit']",
                "input[value='Search']", "button:has-text('Search')",
                "#btnSearch", "#search",
            ]
            for sel in submit_sels:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        await wait_for_content(page, timeout=30_000)
                        break
                except Exception:
                    continue

            await asyncio.sleep(1.5)

            # Scrape result table
            tenders = await self._scrape_table(page, scope, max_pages=None, progress_cb=None)

        except Exception as e:
            log.debug(f"[form-search] date range {from_date}-{to_date}: {e}")

        return tenders

    async def _fill_field(self, page: Page, selectors: list[str], value: str):
        """Try multiple selectors to fill a form field."""
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.fill("")
                    await el.type(value, delay=random.randint(50, 120))
                    return
            except Exception:
                continue

    # ── Strategy 3: Direct page table scrape ──────────────────────────────────

    async def _try_page_scrape(self, scope: str, max_pages, progress_cb) -> list[dict]:
        """Directly scrape the archive/awards page tables."""
        cfg = self.config
        url = cfg.archive_url if scope == "archive" else cfg.awards_url
        if not url:
            return []

        tenders = []
        try:
            ctx  = await self.session.new_context(portal_id=cfg.portal_id)
            page = await self.session.new_page(ctx, portal_id=cfg.portal_id)

            await warmup_session_cookies(page, cfg.base_url)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await wait_for_content(page)

            # Solve any CAPTCHA on the page
            await solve_any_captcha(page)

            tenders = await self._scrape_table(page, scope, max_pages, progress_cb)

            await self.session.save_cookies(ctx, cfg.portal_id)
            await ctx.close()

        except Exception as e:
            log.warning(f"[page-scrape] {cfg.portal_id}: {e}")

        return tenders

    # ── Table scraper (shared) ────────────────────────────────────────────────

    async def _scrape_table(
        self, page: Page, scope: str,
        max_pages, progress_cb
    ) -> list[dict]:
        """Scrape paginated table from current page."""
        tenders   = []
        page_num  = 0
        max_p     = max_pages or 999
        status    = "Awarded" if scope == "awards" else "Archive"

        while page_num < max_p:
            page_num += 1

            await human_scroll(page, scrolls=2)

            # Try multiple table selectors
            table_sels = [
                "table.list_table", "table#tenderTable",
                "table.tablesorter", "table[class*='tender']",
                ".tableContainer table", "table.table",
                "#resultTable", "table",
            ]

            rows_data: list[dict] = []
            for tsel in table_sels:
                try:
                    rows = page.locator(f"{tsel} tbody tr")
                    count = await rows.count()
                    if count < 1:
                        continue

                    for i in range(count):
                        row = rows.nth(i)
                        cells = []
                        cell_els = row.locator("td")
                        cell_count = await cell_els.count()
                        for j in range(cell_count):
                            txt = await cell_els.nth(j).inner_text()
                            cells.append(txt.strip())

                        if len(cells) < 3:
                            continue

                        # Get detail URL if present
                        detail_url = ""
                        try:
                            link = row.locator("a").first
                            if await link.count() > 0:
                                href = await link.get_attribute("href")
                                if href and not href.startswith("javascript"):
                                    detail_url = href if href.startswith("http") else \
                                        self.config.base_url.rstrip("/") + "/" + href.lstrip("/")
                        except Exception:
                            pass

                        if scope == "awards":
                            t = _parse_award_row(cells, self.config.portal_id, self.config.display_name)
                        else:
                            t = _parse_gepnic_row(cells, self.config.portal_id, self.config.display_name, status)

                        if detail_url:
                            t["detail_url"] = detail_url

                        # Filter out header rows / empty rows
                        if t.get("title") and t["title"].lower() not in ("title", "tender title", "subject", "s.no", "sr.no"):
                            rows_data.append(t)

                    if rows_data:
                        break  # Found the right table

                except Exception as e:
                    log.debug(f"[table] selector {tsel}: {e}")
                    continue

            if not rows_data:
                log.debug(f"[table] No rows found on page {page_num}")
                break

            tenders.extend(rows_data)
            log.info(f"[table] Page {page_num}: {len(rows_data)} rows ({len(tenders)} total)")

            if progress_cb:
                await progress_cb(page_num, len(tenders))

            # Try to go to next page
            went_next = await self._next_page(page)
            if not went_next:
                break

            await asyncio.sleep(random.uniform(2.0, 4.0))

        return tenders

    async def _next_page(self, page: Page) -> bool:
        """Click 'Next' pagination button. Returns True if navigated."""
        next_sels = [
            "a:has-text('Next')", "a:has-text('next')",
            "a:has-text('>')", "a:has-text('»')",
            ".pagination a[aria-label='Next']",
            "a.next", "li.next a",
            "input[value='Next']",
            "#nextPage", ".nextPage",
            "a[title='Next Page']",
        ]
        for sel in next_sels:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    is_disabled = await el.get_attribute("class") or ""
                    if "disabled" in is_disabled.lower():
                        return False
                    await el.click()
                    await wait_for_content(page, timeout=20_000)
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    return True
            except Exception:
                continue
        return False
