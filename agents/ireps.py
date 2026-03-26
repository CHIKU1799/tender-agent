"""
IREPS Agent — Indian Railways e-Procurement System (ireps.gov.in)
Platform: Apache Struts 2 / custom Java web app
Strategy: Playwright — navigate public tender search, extract table
"""
from __future__ import annotations
import logging
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso, retry_async
from portals.configs import PortalConfig

log = logging.getLogger("ireps")

SEARCH_URL = "https://www.ireps.gov.in/ireps/tender/tenderSearchPublic.action"

EXTRACT_JS = """() => {
    const rows = document.querySelectorAll('table.dataTable tbody tr, table#tenderList tbody tr');
    return Array.from(rows).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const link = row.querySelector('a');
        return {
            tender_id:      cells[0]?.innerText.trim() || '',
            title:          cells[1]?.innerText.trim() || '',
            organisation:   cells[2]?.innerText.trim() || '',
            published_date: cells[3]?.innerText.trim() || '',
            closing_date:   cells[4]?.innerText.trim() || '',
            detail_href:    link ? link.href : '',
        };
    }).filter(r => r.tender_id || r.title);
}"""

PAGINATION_JS = """() => {
    const next = document.querySelector('a.next, a[title="Next"], input[value="Next"]');
    const info = document.querySelector('#tenderList_info, .dataTables_info');
    return {
        hasNext: !!next,
        infoText: info ? info.innerText.trim() : null,
    };
}"""


class IREPSAgent(BaseAgent):
    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id="ireps")
        ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            log.info("[ireps] Loading public tender search...")
            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # Try clicking "Search" with default (empty) params
            search_btn = await page.query_selector(
                'input[type="submit"][value*="Search"], button:text("Search")'
            )
            if search_btn:
                await search_btn.click()
                await page.wait_for_timeout(3000)

            current_page = 1
            while True:
                if max_pages and current_page > max_pages:
                    break

                rows = await page.evaluate(EXTRACT_JS)
                if not rows:
                    log.warning(f"[ireps] Page {current_page}: no rows found")
                    break

                tenders = [self._parse_row(r, current_page) for r in rows]
                if org_filter:
                    tenders = [t for t in tenders if org_filter.lower() in t["organisation"].lower()]

                result.tenders.extend(tenders)
                result.pages = current_page

                log.info(f"[ireps] Page {current_page} — {len(tenders)} tenders (total: {len(result.tenders)})")
                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                pagination = await page.evaluate(PAGINATION_JS)
                if not pagination["hasNext"]:
                    break

                await random_delay()
                try:
                    async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                        await page.click('a.next, a[title="Next"]')
                    current_page += 1
                except Exception as e:
                    log.warning(f"[ireps] Navigation failed: {e}")
                    break

        except Exception as e:
            log.error(f"[ireps] Error: {e}")
            result.errors.append(str(e))
        finally:
            await ctx.close()

        return result

    def _parse_row(self, row: dict, page_num: int) -> dict:
        return {
            "portal_id":      "ireps",
            "tender_id":      row.get("tender_id", ""),
            "ref_number":     row.get("tender_id", ""),
            "title":          row.get("title", ""),
            "organisation":   row.get("organisation", ""),
            "published_date": row.get("published_date", ""),
            "closing_date":   row.get("closing_date", ""),
            "opening_date":   "",
            "status":         "Active",
            "detail_url":     row.get("detail_href", ""),
            "scraped_at":     now_iso(),
            "page_num":       page_num,
        }
