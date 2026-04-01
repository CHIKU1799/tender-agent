"""
Generic Agent — auto-detect table structure for BHEL, ONGC, HAL, NHM, MeitY, Education.
Uses heuristic column mapping and saves a debug screenshot on first run.
"""
from __future__ import annotations
import logging
from pathlib import Path
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("generic")

AUTO_JS = """() => {
    const tables = Array.from(document.querySelectorAll('table'));
    let best = null, maxRows = 0;
    for (const t of tables) {
        const n = t.querySelectorAll('tr').length;
        if (n > maxRows) { maxRows = n; best = t; }
    }
    if (!best || maxRows < 3) return { rows: [], headers: [], note: 'No table found' };

    const headerCells = Array.from((best.querySelector('tr th') ? best.querySelector('tr') : best.querySelector('thead tr') || best.querySelector('tr'))?.querySelectorAll('th, td') || []);
    const headers = headerCells.map(c => c.innerText.trim().toLowerCase());

    const dataRows = Array.from(best.querySelectorAll('tbody tr, tr')).filter(r => !r.querySelector('th'));
    const rows = dataRows.slice(0, 500).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const link  = row.querySelector('a');
        return {
            cells:       cells.map(c => c.innerText.trim().replace(/\\s+/g, ' ')),
            detail_href: link ? link.href : ''
        };
    }).filter(r => r.cells.some(c => c && c.length > 1));

    return { rows, headers, rowCount: rows.length };
}"""

NEXT_JS = """() => {
    const btns = [
        document.querySelector('a[title*="Next"], a[title*="next"]'),
        document.querySelector('a.next, a.nextPage, a[rel="next"]'),
        document.querySelector('input[value=">"], input[value="Next"]'),
        Array.from(document.querySelectorAll('a')).find(a =>
            ['>', '>>', 'next', 'next page'].includes(a.innerText.trim().toLowerCase())
        ),
    ].filter(Boolean);
    if (!btns.length) return null;
    const b = btns[0];
    return b.id || b.className || b.href || b.innerText.trim();
}"""

HEURISTIC_MAP = [
    (["id","no","number","sr"],                    "tender_id"),
    (["title","description","work","subject"],      "title"),
    (["org","department","ministry","authority"],   "organisation"),
    (["published","issue","start","from"],          "published_date"),
    (["clos","end","due","last","deadline"],        "closing_date"),
    (["open","open date"],                          "opening_date"),
    (["value","amount","cost","estimate"],          "tender_value_inr"),
    (["emd","earnest"],                             "emd_inr"),
    (["location","district","state","city"],        "location"),
    (["type","category"],                           "tender_type"),
]


class GenericAgent(BaseAgent):

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

        result = ScrapeResult(portal_id=self.portal_id)
        url = self.config.results_url or self.config.base_url
        if not url:
            result.skipped = True
            result.skip_reason = "No URL configured"
            return result

        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            log.info(f"[{self.portal_id}] Loading: {url}")
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # Save debug screenshot on first run
            ss_path = Path("screenshots") / f"{self.portal_id}_debug.png"
            await page.screenshot(path=str(ss_path), full_page=True)
            log.info(f"[{self.portal_id}] Debug screenshot: {ss_path}")

            current_page = 1
            while True:
                if max_pages and current_page > max_pages:
                    break

                data = await page.evaluate(AUTO_JS)
                rows    = data.get("rows", [])
                headers = data.get("headers", [])

                if not rows:
                    note = data.get("note", "no rows")
                    log.warning(f"[{self.portal_id}] Page {current_page}: {note}")
                    result.skip_reason = note
                    break

                tenders = [self._parse_row(r, headers, current_page) for r in rows]
                if org_filter:
                    tenders = [t for t in tenders if org_filter.lower() in t.get("organisation","").lower()]

                result.tenders.extend(tenders)
                result.pages = current_page
                log.info(f"[{self.portal_id}] Page {current_page} — {len(tenders)} rows")

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                next_info = await page.evaluate(NEXT_JS)
                if not next_info:
                    break

                await random_delay()
                try:
                    async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                        await page.evaluate("""() => {
                            const b = document.querySelector('a[title*="Next"], a.next, a.nextPage')
                                   || Array.from(document.querySelectorAll('a')).find(a => ['>', 'next'].includes(a.innerText.trim().toLowerCase()));
                            if (b) b.click();
                        }""")
                    current_page += 1
                except Exception:
                    break

        except Exception as e:
            log.error(f"[{self.portal_id}] Error: {e}")
            await page.screenshot(path=f"screenshots/{self.portal_id}_error.png")
            result.errors.append(str(e))
            result.skip_reason = str(e)[:200]
        finally:
            await ctx.close()

        return result

    def _parse_row(self, row: dict, headers: list, page_num: int) -> dict:
        cells = row.get("cells", [])
        # Build header→value map
        hmap = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))} if headers else {}
        for i, c in enumerate(cells):
            hmap[f"col_{i}"] = c

        def find(*patterns) -> str:
            for pat in patterns:
                for k, v in hmap.items():
                    if any(p in k for p in pat) and v:
                        return v
            return ""

        # Apply heuristic column mapping
        mapped: dict[str, str] = {}
        for patterns, field in HEURISTIC_MAP:
            mapped[field] = find(patterns)

        # Positional fallbacks
        def c(i): return cells[i] if i < len(cells) else ""

        return {
            "portal_id":            self.portal_id,
            "portal_name":          self.config.display_name,
            "tender_id":            mapped.get("tender_id") or c(0),
            "ref_number":           mapped.get("tender_id") or c(0),
            "title":                mapped.get("title") or c(1),
            "organisation":         mapped.get("organisation") or c(2),
            "published_date":       mapped.get("published_date") or c(3),
            "closing_date":         mapped.get("closing_date") or c(4),
            "opening_date":         mapped.get("opening_date") or "",
            "status":               "Active",
            "detail_url":           row.get("detail_href", ""),
            "scraped_at":           now_iso(),
            "page_num":             page_num,
            "detail_scraped":       False,
            "tender_value_inr":     mapped.get("tender_value_inr", ""),
            "tender_fee_inr":       "",
            "emd_inr":              mapped.get("emd_inr", ""),
            "tender_type":          mapped.get("tender_type", ""),
            "tender_category":      "",
            "product_category":     "",
            "form_of_contract":     "",
            "payment_mode":         "",
            "bid_submission_start": "",
            "bid_submission_end":   "",
            "doc_download_start":   "",
            "doc_download_end":     "",
            "location":             mapped.get("location", ""),
            "pincode":              "",
            "contact":              "",
            "documents":            "",
        }
