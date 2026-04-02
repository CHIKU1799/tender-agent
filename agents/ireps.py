"""
IREPS Agent — Indian Railways e-Procurement System (ireps.gov.in)
Platform: Apache Struts 2 / custom Java — public tender search page.
"""
from __future__ import annotations
import logging
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso, retry_async
from portals.configs import PortalConfig

log = logging.getLogger("ireps")

# Public search page — no login needed
SEARCH_URL = "https://www.ireps.gov.in/ireps/tender/tenderSearchPublic.action"

EXTRACT_JS = """() => {
    // IREPS uses a standard HTML table
    const table = document.querySelector('table.list, table#tenderList, table.dataTable, .tenderTable');
    if (!table) {
        // Fallback: find biggest table
        const tables = Array.from(document.querySelectorAll('table'));
        let best = null, max = 0;
        for (const t of tables) {
            const n = t.querySelectorAll('tr').length;
            if (n > max) { max = n; best = t; }
        }
        if (!best || max < 3) return [];
        const rows = Array.from(best.querySelectorAll('tr')).slice(1);
        return rows.map(row => {
            const cells = Array.from(row.querySelectorAll('td'));
            const link  = row.querySelector('a');
            return {
                cells:      cells.map(c => c.innerText.trim()),
                detail_href: link ? link.href : ''
            };
        }).filter(r => r.cells.some(c => c));
    }
    const rows = Array.from(table.querySelectorAll('tbody tr, tr')).slice(1);
    return rows.map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const link  = row.querySelector('a');
        return {
            cells:       cells.map(c => c.innerText.trim()),
            detail_href: link ? link.href : ''
        };
    }).filter(r => r.cells.some(c => c));
}"""

NEXT_JS = """() => {
    const n = document.querySelector('a.next, a[title="Next Page"], a[title="Next"], input[value="Next >"]');
    return n ? n.outerHTML : null;
}"""


class IREPSAgent(BaseAgent):

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id="ireps")
        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            log.info("[ireps] Loading public search page...")
            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # Try submitting the blank search form
            for sel in ['input[type="submit"]', 'button[type="submit"]', 'input[value*="Search"]']:
                btn = await page.query_selector(sel)
                if btn:
                    try:
                        async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                            await btn.click()
                        await page.wait_for_timeout(2000)
                        break
                    except Exception:
                        pass

            current_page = 1
            while True:
                if max_pages and current_page > max_pages:
                    break

                rows = await page.evaluate(EXTRACT_JS)
                if not rows:
                    log.warning(f"[ireps] Page {current_page}: no rows — trying screenshot")
                    await page.screenshot(path="screenshots/ireps_debug.png")
                    break

                tenders = [self._parse_row(r, current_page) for r in rows]
                if org_filter:
                    tenders = [t for t in tenders if org_filter.lower() in t["organisation"].lower()]

                result.tenders.extend(tenders)
                result.pages = current_page
                log.info(f"[ireps] Page {current_page} — {len(tenders)} tenders (total: {len(result.tenders)})")

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                next_html = await page.evaluate(NEXT_JS)
                if not next_html:
                    break

                await random_delay()
                try:
                    async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                        await page.evaluate("""() => {
                            const n = document.querySelector('a.next, a[title="Next Page"], a[title="Next"]');
                            if (n) n.click();
                        }""")
                    current_page += 1
                    await page.wait_for_timeout(1500)
                except Exception as e:
                    log.warning(f"[ireps] Pagination failed: {e}")
                    break

        except Exception as e:
            log.error(f"[ireps] Error: {e}")
            await page.screenshot(path="screenshots/ireps_error.png")
            result.errors.append(str(e))
        finally:
            await ctx.close()

        return result

    def _parse_row(self, row: dict, page_num: int) -> dict:
        cells = row.get("cells", [])
        def cell(i): return cells[i].strip() if i < len(cells) else ""
        return {
            "portal_id":            "ireps",
            "portal_name":          "Indian Railways e-Procurement (IREPS)",
            "tender_id":            cell(0),
            "ref_number":           cell(0),
            "title":                cell(1),
            "organisation":         cell(2),
            "published_date":       cell(3),
            "closing_date":         cell(4),
            "opening_date":         cell(5) if len(cells) > 5 else "",
            "status":               "Active",
            "detail_url":           row.get("detail_href", ""),
            "scraped_at":           now_iso(),
            "page_num":             page_num,
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
        }
