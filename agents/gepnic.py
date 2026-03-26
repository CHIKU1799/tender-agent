"""
GePNIC Agent — works for ALL NIC GePNIC portals:
  defproc, cppp, etenders, ntpc, coalindia,
  karnataka, maharashtra, up, tamilnadu, gujarat, rajasthan ...

CAPTCHA Bypass Strategy:
  Every NIC GePNIC portal uses the Apache Tapestry framework.
  The search form requires a CAPTCHA — BUT the results DirectLink component
  renders the current session's result set without validating that a search
  was explicitly submitted. So:

    1. Hit any page → server issues JSESSIONID cookie
    2. Navigate directly to:
       {base_url}?component=$DirectLink&page=FrontEndAdvancedSearchResult&service=direct
    3. Server returns ALL active tenders (default = no filter = everything)
    4. Paginate via #linkFwd (Next) until it disappears

  No CAPTCHA solving needed. No form submission required.
"""
from __future__ import annotations
import asyncio
import random
import logging
from datetime import datetime

from playwright.async_api import TimeoutError as PWTimeout

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import parse_title_cell, now_iso, retry_async
from portals.configs import PortalConfig

log = logging.getLogger("gepnic")

# ── JavaScript executed inside the browser page ───────────────────────────────

EXTRACT_ROWS_JS = """(rowSelector, colMap, linkTitle) => {
    const rows = document.querySelectorAll(rowSelector);
    return Array.from(rows).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const linkEl = row.querySelector('a[title="' + linkTitle + '"]');
        return {
            sno:            cells[colMap.sno]           ?.innerText.trim() || '',
            published_date: cells[colMap.published_date]?.innerText.trim() || '',
            closing_date:   cells[colMap.closing_date]  ?.innerText.trim() || '',
            opening_date:   cells[colMap.opening_date]  ?.innerText.trim() || '',
            title_raw:      cells[colMap.title_raw]     ?.innerText.trim() || '',
            organisation:   cells[colMap.organisation]  ?.innerText.trim() || '',
            detail_href:    linkEl ? linkEl.href : '',
        };
    });
}"""

GET_PAGINATION_JS = """() => {
    const next = document.querySelector('#linkFwd');
    const last = document.querySelector('#linkLast');
    const pageLinks = Array.from(document.querySelectorAll('a[id^="linkPage"]'))
        .map(a => ({ id: a.id, text: a.innerText.trim(), href: a.href }));
    const bodyText = document.body.innerText;
    const m = bodyText.match(/Page\\s+(\\d+)\\s+of\\s+(\\d+)/i);
    return {
        hasNext:         !!next,
        hasLast:         !!last,
        pageLinks:       pageLinks,
        paginationText:  m ? m[0] : null,
    };
}"""

DETAIL_EXTRACT_JS = """() => {
    const kvPairs = {};
    document.querySelectorAll('tr').forEach(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        if (cells.length >= 2) {
            const key = cells[0].innerText.trim().replace(/:$/, '');
            const val = cells[1].innerText.trim();
            if (key && val && key.length < 80 && !key.includes('\\n')) {
                kvPairs[key] = val;
            }
        }
    });
    const docLinks = Array.from(
        document.querySelectorAll('a[href*="document"], a[href*="Document"], a[href*=".pdf"]')
    ).map(a => ({ text: a.innerText.trim(), href: a.href }));
    return { kvPairs, docLinks };
}"""


class GePNICAgent(BaseAgent):
    """Universal agent for any NIC GePNIC portal."""

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.portal_id)
        ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            # ── Step 1: Seed session (establish JSESSIONID) ──────────────────
            log.info(f"[{self.portal_id}] Seeding session...")
            await self._goto(page, self.config.session_seed_url)
            await random_delay(1.5, 3.0)

            # ── Step 2: Navigate to results (CAPTCHA bypass) ─────────────────
            log.info(f"[{self.portal_id}] Navigating to results (captcha-free)...")
            await self._goto(page, self.config.results_url)
            await page.wait_for_timeout(2000)

            current_page = 1
            empty_streak = 0

            while True:
                if max_pages and current_page > max_pages:
                    log.info(f"[{self.portal_id}] Reached page limit ({max_pages})")
                    break

                # ── Extract rows ──────────────────────────────────────────────
                rows = await page.evaluate(
                    EXTRACT_ROWS_JS,
                    self.config.row_selector,
                    self.config.col_map,
                    "View Tender Information",
                )

                if not rows:
                    empty_streak += 1
                    log.warning(f"[{self.portal_id}] Page {current_page}: no rows (empty_streak={empty_streak})")
                    if empty_streak >= 2:
                        break
                else:
                    empty_streak = 0

                tenders = self._parse_rows(rows, current_page)

                if org_filter:
                    tenders = [
                        t for t in tenders
                        if org_filter.lower() in t["organisation"].lower()
                    ]

                result.tenders.extend(tenders)
                result.pages = current_page

                # ── Pagination check ──────────────────────────────────────────
                pagination = await page.evaluate(GET_PAGINATION_JS)
                page_text = pagination.get("paginationText") or f"Page {current_page}"
                log.info(
                    f"[{self.portal_id}] {page_text} — "
                    f"{len(tenders)} tenders (total: {len(result.tenders)})"
                )

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                if not pagination["hasNext"]:
                    log.info(f"[{self.portal_id}] No Next button — reached last page")
                    break

                # ── Delay + rotate UA ─────────────────────────────────────────
                await random_delay()
                await self.session.rotate_ua(ctx)

                # ── Click Next ────────────────────────────────────────────────
                clicked = await self._click_next(page)
                if not clicked:
                    break

                current_page += 1
                await page.wait_for_timeout(1500)

        except Exception as e:
            log.error(f"[{self.portal_id}] Scrape error: {e}")
            result.errors.append(str(e))
        finally:
            await ctx.close()

        return result

    async def scrape_detail(self, tender: dict) -> dict:
        """Enrich a tender dict with detail page data."""
        url = tender.get("detail_url", "")
        if not url:
            return {**tender, "detail_scraped": False}

        ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)
        try:
            await self._goto(page, url)
            await page.wait_for_timeout(1500)
            data = await page.evaluate(DETAIL_EXTRACT_JS)
            kv = data.get("kvPairs", {})

            def kv_get(*keys):
                for k in keys:
                    if kv.get(k):
                        return kv[k]
                return ""

            enriched = {
                **tender,
                "detail_scraped":       True,
                "detail_fetched_at":    now_iso(),
                "tender_value":         kv_get("Tender Value in \u20b9", "ECV", "Estimated Cost (In Lakhs)"),
                "tender_fee":           kv_get("Tender Fee in \u20b9", "Tender Fee"),
                "emd":                  kv_get("EMD Amount in \u20b9", "EMD Amount (in \u20b9)"),
                "tender_type":          kv_get("Tender Type"),
                "tender_category":      kv_get("Tender Category"),
                "form_of_contract":     kv_get("Form Of Contract"),
                "product_category":     kv_get("Product Category"),
                "payment_mode":         kv_get("Payment Mode"),
                "bid_submission_start": kv_get("Bid Submission Start Date"),
                "bid_submission_end":   kv_get("Bid Submission End Date"),
                "document_sale_start":  kv_get("Document Download / Sale Start Date"),
                "document_sale_end":    kv_get("Document Download / Sale End Date"),
                "location":             kv_get("Location", "District"),
                "pincode":              kv_get("Pincode"),
                "contact":              kv_get("Contact", "Contact Person"),
                "documents":            [d["href"] for d in data.get("docLinks", [])],
                "kv_pairs":             kv,
            }
            return enriched
        except Exception as e:
            log.error(f"[{self.portal_id}] Detail scrape error for {url}: {e}")
            return {**tender, "detail_scraped": False, "error": str(e)}
        finally:
            await ctx.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

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
        tenders = []
        for row in rows:
            parsed = parse_title_cell(row.get("title_raw", ""))
            tenders.append({
                "portal_id":      self.portal_id,
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
            })
        return tenders
