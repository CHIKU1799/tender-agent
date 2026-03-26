"""
Generic Agent — for portals needing custom exploration (BHEL, ONGC, HAL, NHM, MeitY, Education).
Attempts to:
  1. Load the portal's results/tenders page
  2. Auto-detect table structure using heuristics
  3. Extract whatever tabular data is available
  4. Paginate if a Next button is found

Returns partial data with a note that manual inspection may improve results.
"""
from __future__ import annotations
import logging
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("generic")

AUTO_EXTRACT_JS = """() => {
    // Find the biggest table on the page — likely the data table
    const tables = Array.from(document.querySelectorAll('table'));
    let best = null, maxRows = 0;
    for (const t of tables) {
        const rowCount = t.querySelectorAll('tr').length;
        if (rowCount > maxRows) { maxRows = rowCount; best = t; }
    }
    if (!best || maxRows < 3) return { rows: [], headers: [], note: 'No suitable table found' };

    const headerRow = best.querySelector('tr:first-child');
    const headers = Array.from(headerRow?.querySelectorAll('th, td') || [])
        .map(c => c.innerText.trim());

    const dataRows = Array.from(best.querySelectorAll('tr')).slice(1);
    const rows = dataRows.map(row => {
        const cells = Array.from(row.querySelectorAll('td, th'));
        const link = row.querySelector('a');
        return {
            cells: cells.map(c => c.innerText.trim()),
            href:  link ? link.href : '',
        };
    }).filter(r => r.cells.some(c => c));

    return { rows, headers, rowCount: rows.length };
}"""

NEXT_BTN_JS = """() => {
    const candidates = [
        document.querySelector('a[title*="Next"], a[title*="next"]'),
        document.querySelector('a.next, a.nextPage'),
        document.querySelector('input[value="Next"], input[value=">"]'),
        Array.from(document.querySelectorAll('a')).find(
            a => a.innerText.trim() === '>' || a.innerText.trim().toLowerCase() === 'next'
        ),
    ].filter(Boolean);
    return candidates.length > 0 ? { found: true, selector: candidates[0].id || 'a.next' } : { found: false };
}"""


class GenericAgent(BaseAgent):
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

        if not self.config.results_url:
            result.skipped = True
            result.skip_reason = "No results_url configured — needs manual exploration"
            return result

        ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)
        current_page = 1

        try:
            log.info(f"[{self.portal_id}] Loading: {self.config.results_url}")
            await page.goto(self.config.results_url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            while True:
                if max_pages and current_page > max_pages:
                    break

                data = await page.evaluate(AUTO_EXTRACT_JS)
                rows = data.get("rows", [])
                headers = data.get("headers", [])

                if not rows:
                    log.warning(f"[{self.portal_id}] Page {current_page}: {data.get('note', 'no rows')}")
                    break

                tenders = [
                    self._make_tender(r, headers, current_page)
                    for r in rows
                ]
                if org_filter:
                    tenders = [t for t in tenders if org_filter.lower() in t.get("organisation", "").lower()]

                result.tenders.extend(tenders)
                result.pages = current_page

                log.info(f"[{self.portal_id}] Page {current_page} — {len(tenders)} rows (total: {len(result.tenders)})")
                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                # Try to find Next button
                next_info = await page.evaluate(NEXT_BTN_JS)
                if not next_info["found"]:
                    break

                await random_delay()
                try:
                    async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                        await page.evaluate("document.querySelector('a[title*=\"Next\"], a.next, a.nextPage')?.click()")
                    current_page += 1
                except Exception:
                    break

        except Exception as e:
            log.error(f"[{self.portal_id}] Error: {e}")
            result.errors.append(str(e))
            result.skip_reason = f"Platform needs manual exploration: {e}"
        finally:
            await ctx.close()

        return result

    def _make_tender(self, row: dict, headers: list, page_num: int) -> dict:
        cells = row.get("cells", [])
        # Map cells to headers where available, otherwise use positional keys
        cell_map = {}
        for i, cell in enumerate(cells):
            key = headers[i].lower().replace(" ", "_") if i < len(headers) else f"col_{i}"
            cell_map[key] = cell

        # Heuristic field mapping
        def find(*patterns):
            for pat in patterns:
                for k, v in cell_map.items():
                    if pat in k and v:
                        return v
            return cells[0] if cells else ""

        return {
            "portal_id":      self.portal_id,
            "tender_id":      find("tender_id", "id", "number", "no"),
            "ref_number":     find("ref", "number", "no"),
            "title":          find("title", "description", "work", "subject"),
            "organisation":   find("organisation", "department", "ministry", "unit"),
            "published_date": find("published", "start", "issue"),
            "closing_date":   find("closing", "end", "due", "last"),
            "opening_date":   find("opening", "open"),
            "status":         "Active",
            "detail_url":     row.get("href", ""),
            "scraped_at":     now_iso(),
            "page_num":       page_num,
            "_raw":           cell_map,
        }
