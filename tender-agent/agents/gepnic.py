"""
GePNIC Agent — works for ALL NIC GePNIC portals:
  defproc, cppp, etenders, ntpc, coalindia,
  karnataka, maharashtra, up, tamilnadu, gujarat, rajasthan ...

CAPTCHA Bypass:
  Navigate directly to the results DirectLink URL after seeding a session.
  No form submission = no CAPTCHA check. Works on every NIC GePNIC portal.
"""
from __future__ import annotations
import asyncio
import logging
import random
from datetime import datetime

from playwright.async_api import TimeoutError as PWTimeout

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import parse_title_cell, now_iso, retry_async
from portals.configs import PortalConfig

log = logging.getLogger("gepnic")

# ─── JS runs inside the browser page — single-arg pattern for Playwright Python ──

# Accepts a config dict as the single argument
EXTRACT_ROWS_JS = """(cfg) => {
    const rows = document.querySelectorAll(cfg.rowSelector);
    return Array.from(rows).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const linkEl = row.querySelector('a[title="' + cfg.linkTitle + '"]');
        const m = cfg.colMap;
        return {
            sno:            cells[m.sno]            ? cells[m.sno].innerText.trim()            : '',
            published_date: cells[m.published_date] ? cells[m.published_date].innerText.trim() : '',
            closing_date:   cells[m.closing_date]   ? cells[m.closing_date].innerText.trim()   : '',
            opening_date:   cells[m.opening_date]   ? cells[m.opening_date].innerText.trim()   : '',
            title_raw:      cells[m.title_raw]      ? cells[m.title_raw].innerText.trim()      : '',
            organisation:   cells[m.organisation]   ? cells[m.organisation].innerText.trim()   : '',
            detail_href:    linkEl ? linkEl.href : '',
        };
    }).filter(r => r.title_raw || r.detail_href);
}"""

GET_PAGINATION_JS = """() => {
    const next = document.querySelector('#linkFwd');
    const last = document.querySelector('#linkLast');
    const bodyText = document.body.innerText;
    const m = bodyText.match(/Page\\s+(\\d+)\\s+of\\s+(\\d+)/i);
    return {
        hasNext:        !!next,
        hasLast:        !!last,
        paginationText: m ? m[0] : null,
    };
}"""

DETAIL_EXTRACT_JS = """() => {
    const kv = {};
    document.querySelectorAll('tr').forEach(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        if (cells.length >= 2) {
            const key = cells[0].innerText.trim().replace(/:$/, '').trim();
            const val = cells[1].innerText.trim();
            if (key && val && key.length < 80 && !key.includes('\\n') && !key.match(/^\\d+$/)) {
                kv[key] = val;
            }
        }
    });
    const docs = Array.from(
        document.querySelectorAll('a[href*="document"], a[href*="Document"], a[href*=".pdf"], a[href*="NIT"]')
    ).map(a => a.href).filter(Boolean);
    return { kv, docs };
}"""


class GePNICAgent(BaseAgent):
    """Universal agent for any NIC GePNIC portal — configured via PortalConfig."""

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session
        self._js_cfg = {
            "rowSelector": config.row_selector,
            "colMap":      config.col_map,
            "linkTitle":   "View Tender Information",
        }

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.portal_id)
        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            # ── 1. Seed session (get JSESSIONID) ────────────────────────────
            log.info(f"[{self.portal_id}] Seeding session...")
            await self._goto(page, self.config.session_seed_url)
            await random_delay(1.5, 3.0)

            # ── 2. Direct results URL — CAPTCHA bypass ───────────────────────
            log.info(f"[{self.portal_id}] Navigating to results (no captcha)...")
            await self._goto(page, self.config.results_url)
            await page.wait_for_timeout(2000)

            current_page = 1
            empty_streak = 0

            while True:
                if max_pages and current_page > max_pages:
                    log.info(f"[{self.portal_id}] Reached page limit ({max_pages})")
                    break

                rows = await page.evaluate(EXTRACT_ROWS_JS, self._js_cfg)

                if not rows:
                    empty_streak += 1
                    log.warning(f"[{self.portal_id}] Page {current_page}: 0 rows (streak={empty_streak})")
                    if empty_streak >= 2:
                        break
                else:
                    empty_streak = 0
                    tenders = self._parse_rows(rows, current_page)
                    if org_filter:
                        tenders = [t for t in tenders if org_filter.lower() in t["organisation"].lower()]
                    result.tenders.extend(tenders)

                pagination = await page.evaluate(GET_PAGINATION_JS)
                ptext = pagination.get("paginationText") or f"Page {current_page}"
                log.info(f"[{self.portal_id}] {ptext} — {len(result.tenders)} total")

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                result.pages = current_page

                if not pagination["hasNext"]:
                    log.info(f"[{self.portal_id}] Last page reached.")
                    break

                await random_delay()
                await self.session.rotate_ua(ctx)

                if not await self._click_next(page):
                    break

                current_page += 1
                await page.wait_for_timeout(1500)

            # ── 3. Optionally enrich with detail pages (same context = same session) ──
            if fetch_details and result.tenders:
                log.info(f"[{self.portal_id}] Fetching detail pages for {len(result.tenders)} tenders...")
                result.tenders = await self._fetch_all_details(result.tenders, ctx)

        except Exception as e:
            log.error(f"[{self.portal_id}] Listing error: {e}")
            result.errors.append(str(e))
        finally:
            await ctx.close()

        return result

    # ── Detail scraping ────────────────────────────────────────────────────────

    async def _fetch_all_details(self, tenders: list[dict], ctx=None) -> list[dict]:
        """Fetch detail pages. Pass the listing ctx to reuse the same session cookies."""
        own_ctx = ctx is None
        if own_ctx:
            ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)
        enriched = []
        try:
            for i, tender in enumerate(tenders, 1):
                log.info(f"[{self.portal_id}] Detail {i}/{len(tenders)}: {tender.get('tender_id','?')}")
                enriched.append(await self._scrape_one_detail(page, tender))
                if i < len(tenders):
                    await asyncio.sleep(random.uniform(2.5, 4.0))
        finally:
            await page.close()
            if own_ctx:
                await ctx.close()
        return enriched

    async def _scrape_one_detail(self, page, tender: dict) -> dict:
        url = tender.get("detail_url", "")
        if not url:
            return {**tender, "detail_scraped": False, "detail_error": "no URL"}
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(1000)
            data = await page.evaluate(DETAIL_EXTRACT_JS)
            return self._merge_detail(tender, data)
        except Exception as e:
            log.warning(f"[{self.portal_id}] Detail failed {url[:60]}: {e}")
            return {**tender, "detail_scraped": False, "detail_error": str(e)[:120]}

    def _merge_detail(self, tender: dict, data: dict) -> dict:
        kv = data.get("kv", {})

        def get(*keys):
            for k in keys:
                v = kv.get(k, "")
                if v:
                    return v.replace("\n", " ").strip()
            return ""

        return {
            **tender,
            "detail_scraped":         True,
            "detail_fetched_at":      now_iso(),
            "tender_value_inr":       get("Tender Value in \u20b9", "ECV", "Estimated Cost (In Lakhs)", "Tender Value"),
            "tender_fee_inr":         get("Tender Fee in \u20b9", "Tender Fee"),
            "emd_inr":                get("EMD Amount in \u20b9", "EMD Amount (in \u20b9)", "EMD"),
            "emd_fee_type":           get("EMD Fee Type"),
            "tender_type":            get("Tender Type"),
            "tender_category":        get("Tender Category"),
            "product_category":       get("Product Category"),
            "form_of_contract":       get("Form Of Contract"),
            "payment_mode":           get("Payment Mode"),
            "bid_submission_start":   get("Bid Submission Start Date"),
            "bid_submission_end":     get("Bid Submission End Date"),
            "doc_download_start":     get("Document Download / Sale Start Date"),
            "doc_download_end":       get("Document Download / Sale End Date"),
            "clarification_start":    get("Clarification Start Date"),
            "clarification_end":      get("Clarification End Date"),
            "pre_bid_meeting":        get("Pre Bid Meeting Date"),
            "bid_validity":           get("Bid Validity(Days)"),
            "work_description":       get("Work Description", "Description"),
            "location":               get("Location", "District"),
            "pincode":                get("Pincode"),
            "contact":                get("Contact", "Contact Person"),
            "fee_payable_to":         get("Fee Payable To"),
            "emd_payable_to":         get("EMD Payable To"),
            "two_stage_bid":          get("Two Stage Bidding Allowed"),
            "nda_allowed":            get("NDA Allowed"),
            "documents":              " | ".join(data.get("docs", [])),
            # Award fields — populated only if tender has been awarded
            "award_winner":           get("Award To", "Successful Bidder", "Vendor Name",
                                          "L1 Bidder", "Awarded To"),
            "award_date":             get("Award of Contract Date", "AOC Date",
                                          "Date of Award", "Contract Award Date"),
            "award_amount":           get("Award Amount", "Contract Value",
                                          "Award Value", "Contract Amount"),
            "aoc_no":                 get("AOC No.", "AOC Reference No.",
                                          "Award Reference", "Contract No."),
        }

    # ── Navigation helpers ─────────────────────────────────────────────────────

    @retry_async(max_attempts=3, base_delay=2.0)
    async def _goto(self, page, url: str):
        await page.goto(url, wait_until="networkidle", timeout=60_000)

    async def _click_next(self, page) -> bool:
        for attempt in range(1, 4):
            try:
                async with page.expect_navigation(wait_until="networkidle", timeout=45_000):
                    await page.click("#linkFwd")
                return True
            except Exception as e:
                if attempt == 3:
                    log.error(f"[{self.portal_id}] Next click failed: {e}")
                    return False
                await asyncio.sleep(2 ** attempt)
        return False

    def _parse_rows(self, rows: list[dict], page_num: int) -> list[dict]:
        ts = now_iso()
        results = []
        for row in rows:
            parsed = parse_title_cell(row.get("title_raw", ""))
            results.append({
                "portal_id":      self.portal_id,
                "portal_name":    self.config.display_name,
                "tender_id":      parsed["tender_id"],
                "ref_number":     parsed["ref_number"],
                "title":          parsed["title"],
                "organisation":   row.get("organisation", "").replace("||", " > "),
                "published_date": row.get("published_date", ""),
                "closing_date":   row.get("closing_date", ""),
                "opening_date":   row.get("opening_date", ""),
                "status":         "Active",
                "detail_url":     row.get("detail_href", ""),
                "scraped_at":     ts,
                "page_num":       page_num,
                # Detail fields (empty until enriched)
                "detail_scraped":       False,
                "tender_value_inr":     "",
                "tender_fee_inr":       "",
                "emd_inr":              "",
                "tender_type":          "",
                "tender_category":      "",
                "product_category":     "",
                "form_of_contract":     "",
                "payment_mode":         "",
                "bid_submission_start": "",
                "bid_submission_end":   "",
                "doc_download_start":   "",
                "doc_download_end":     "",
                "location":             "",
                "pincode":              "",
                "contact":              "",
                "documents":            "",
            })
        return results
