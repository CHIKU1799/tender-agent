"""
Gujarat Tender Agents — handles multiple Gujarat sources:

1. tender.nprocure.com  — New Gujarat eProcurement portal (replaced old nprocure.com)
2. gujarattenders.in    — Aggregator site
3. nprocure.com AOC     — Award of Contract details (historical pricing)

Strategy:
  - tender.nprocure.com is a modern web app — intercept API + scrape tables
  - nprocure.com/asp/home/AOCDetailsHome.asp has awarded contracts with pricing
  - gujarattenders.in uses standard HTML tables
"""
from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay, wait_for_content
from core.utils import now_iso

log = logging.getLogger("agents.gujarat")


class GujaratNprocureAgent(BaseAgent):
    """
    Scrapes Gujarat's new tender.nprocure.com portal.
    Modern SPA — intercepts JSON API responses + table fallback.
    Also scrapes AOC (Award of Contract) page for pricing data.
    """

    def __init__(self, config, session: BrowserSession, scope: str = "active"):
        super().__init__(config)
        self.session = session
        self.scope = scope

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result  = ScrapeResult(portal_id=self.portal_id)
        tenders = []

        scopes = ["active", "archive", "awards"] if self.scope in ("both", "all") else \
                 ["archive", "awards"] if self.scope == "both" else [self.scope]

        try:
            ctx  = await self.session.new_context()
            page = await self.session.new_page(ctx)

            for sc in scopes:
                if sc == "awards":
                    batch = await self._scrape_aoc(page, max_pages or 5, org_filter, progress_cb)
                else:
                    batch = await self._scrape_portal(page, sc, max_pages or 5, org_filter, progress_cb)

                status = {"active": "Active", "archive": "Archive", "awards": "Awarded"}.get(sc, "Active")
                for t in batch:
                    t["status"] = status
                tenders.extend(batch)
                log.info(f"[gujarat] {sc}: {len(batch)} tenders")

            await ctx.close()

        except Exception as e:
            log.error(f"[gujarat] {e}")
            result.errors.append(str(e))

        result.tenders = tenders
        return result

    async def _scrape_portal(self, page, scope, max_pages, org_filter, progress_cb):
        """Scrape tender.nprocure.com — modern portal."""
        tenders = []
        url = getattr(self.config, 'results_url', '') or self.config.base_url

        # Intercept API responses
        api_items = []

        async def capture(response):
            if response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        data = await response.json()
                        if isinstance(data, list):
                            api_items.extend(data)
                        elif isinstance(data, dict):
                            for key in ("data", "tenders", "bids", "results", "items", "records",
                                       "content", "tenderList", "bidList"):
                                if isinstance(data.get(key), list):
                                    api_items.extend(data[key])
                                    break
                    except Exception:
                        pass

        page.on("response", capture)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await wait_for_content(page)
            await asyncio.sleep(3)

            # Scroll to trigger lazy loads
            for _ in range(5):
                await page.mouse.wheel(0, 800)
                await asyncio.sleep(1)

            # Try clicking Tenders nav
            for nav_sel in ["a:has-text('Tender')", "[href*='tender']", "a:has-text('e-Tender')"]:
                try:
                    el = page.locator(nav_sel).first
                    if await el.count() > 0:
                        await el.click()
                        await wait_for_content(page)
                        await asyncio.sleep(2)
                        break
                except Exception:
                    continue

            if progress_cb:
                await progress_cb(1, len(api_items))

            # Normalise API items
            for item in api_items:
                t = self._normalise_api(item)
                if t:
                    tenders.append(t)

            # Fallback — scrape table
            if not tenders:
                tenders = await self._scrape_table(page)

        except Exception as e:
            log.error(f"[gujarat] portal scrape: {e}")

        page.remove_listener("response", capture)
        return tenders

    async def _scrape_aoc(self, page, max_pages, org_filter, progress_cb):
        """Scrape nprocure.com AOC (Award of Contract) page — has pricing + awardee."""
        tenders = []
        aoc_url = self.config.awards_url or "https://www.nprocure.com/asp/home/AOCDetailsHome.asp"

        try:
            await page.goto(aoc_url, wait_until="domcontentloaded", timeout=45_000)
            await wait_for_content(page)
            await asyncio.sleep(2)

            # Parse AOC table
            rows = await page.evaluate("""() => {
                const tables = Array.from(document.querySelectorAll("table"));
                let best = null, maxRows = 0;
                for (const t of tables) {
                    const n = t.querySelectorAll("tr").length;
                    if (n > maxRows) { maxRows = n; best = t; }
                }
                if (!best || maxRows < 3) return {rows: [], headers: []};

                const headerCells = Array.from((best.querySelector("tr th")
                    ? best.querySelector("tr")
                    : best.querySelector("thead tr") || best.querySelector("tr"))
                    ?.querySelectorAll("th, td") || []);
                const headers = headerCells.map(c => c.innerText.trim().toLowerCase());

                const dataRows = Array.from(best.querySelectorAll("tbody tr, tr"))
                    .filter(r => !r.querySelector("th") && r.querySelectorAll("td").length >= 3);

                return {
                    rows: dataRows.slice(0, 200).map(row => {
                        const cells = Array.from(row.querySelectorAll("td"));
                        const link = row.querySelector("a");
                        return {
                            cells: cells.map(c => c.innerText.trim()),
                            href: link ? link.href : ""
                        };
                    }),
                    headers
                };
            }""")

            headers = rows.get("headers", [])
            for row in rows.get("rows", []):
                t = self._parse_aoc_row(row, headers)
                if t:
                    if org_filter and org_filter.lower() not in t.get("organisation", "").lower():
                        continue
                    tenders.append(t)

            if progress_cb:
                await progress_cb(1, len(tenders))

        except Exception as e:
            log.error(f"[gujarat] AOC scrape: {e}")

        return tenders

    async def _scrape_table(self, page):
        """Fallback HTML table scrape."""
        tenders = []
        try:
            table_rows = page.locator("table tbody tr")
            count = await table_rows.count()
            for i in range(min(count, 200)):
                cells = []
                tds = table_rows.nth(i).locator("td")
                for j in range(await tds.count()):
                    cells.append((await tds.nth(j).inner_text()).strip())
                if len(cells) < 2:
                    continue
                detail_url = ""
                try:
                    link = table_rows.nth(i).locator("a").first
                    if await link.count() > 0:
                        href = await link.get_attribute("href") or ""
                        detail_url = href if href.startswith("http") else self.config.base_url + "/" + href.lstrip("/")
                except Exception:
                    pass

                tenders.append({
                    "portal_id":        self.portal_id,
                    "portal_name":      self.config.display_name,
                    "source_website":   self.config.base_url,
                    "tender_id":        cells[0],
                    "title":            cells[1] if len(cells) > 1 else "",
                    "organisation":     cells[2] if len(cells) > 2 else "",
                    "closing_date":     cells[3] if len(cells) > 3 else "",
                    "tender_value_inr": cells[4] if len(cells) > 4 else "",
                    "status":           "Active",
                    "detail_url":       detail_url,
                    "scraped_at":       now_iso(),
                    "location":         "Gujarat",
                })
        except Exception as e:
            log.debug(f"[gujarat] table: {e}")
        return tenders

    def _normalise_api(self, item: dict) -> dict | None:
        if not item or not isinstance(item, dict):
            return None

        def g(*keys):
            for k in keys:
                for v in [item.get(k), item.get(k.lower()), item.get(k.upper())]:
                    if v:
                        return str(v).strip()
            return ""

        title = g("title", "name", "tenderTitle", "subject", "workTitle")
        if not title:
            return None

        return {
            "portal_id":        self.portal_id,
            "portal_name":      self.config.display_name,
            "source_website":   self.config.base_url,
            "tender_id":        g("id", "tenderId", "referenceNo"),
            "ref_number":       g("referenceNo", "refNo", "reference"),
            "title":            title,
            "organisation":     g("organisation", "department", "buyer"),
            "state":            "Gujarat",
            "published_date":   g("publishedDate", "startDate", "issueDate"),
            "closing_date":     g("closingDate", "endDate", "dueDate"),
            "tender_value_inr": g("value", "amount", "estimatedValue"),
            "emd_inr":          g("emd", "earnestMoney"),
            "tender_type":      g("type", "tenderType"),
            "status":           g("status") or "Active",
            "detail_url":       g("url", "link", "detailUrl"),
            "scraped_at":       now_iso(),
            "location":         "Gujarat",
        }

    def _parse_aoc_row(self, row: dict, headers: list) -> dict | None:
        cells = row.get("cells", [])
        if len(cells) < 3:
            return None

        # Heuristic column mapping from AOC table headers
        mapped = {}
        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            hl = h.lower()
            if any(k in hl for k in ["tender", "ref", "nit", "number"]):
                mapped["tender_id"] = cells[i]
            elif any(k in hl for k in ["title", "description", "work", "subject"]):
                mapped["title"] = cells[i]
            elif any(k in hl for k in ["org", "dept", "department", "authority"]):
                mapped["organisation"] = cells[i]
            elif any(k in hl for k in ["award", "contract", "aoc"]) and "date" not in hl:
                mapped["award_amount"] = cells[i]
            elif any(k in hl for k in ["winner", "contractor", "bidder", "awarded to"]):
                mapped["award_winner"] = cells[i]
            elif any(k in hl for k in ["date"]) and "closing" not in hl:
                mapped["award_date"] = cells[i]
            elif any(k in hl for k in ["value", "amount", "estimate"]):
                mapped["tender_value_inr"] = cells[i]

        # Fallback positional
        if not mapped.get("title"):
            mapped["title"] = cells[1] if len(cells) > 1 else cells[0]

        return {
            "portal_id":        self.portal_id,
            "portal_name":      self.config.display_name,
            "source_website":   self.config.base_url,
            "tender_id":        mapped.get("tender_id", cells[0]),
            "title":            mapped.get("title", ""),
            "organisation":     mapped.get("organisation", ""),
            "tender_value_inr": mapped.get("tender_value_inr", ""),
            "award_winner":     mapped.get("award_winner", ""),
            "award_amount":     mapped.get("award_amount", ""),
            "award_date":       mapped.get("award_date", ""),
            "status":           "Awarded",
            "detail_url":       row.get("href", ""),
            "scraped_at":       now_iso(),
            "location":         "Gujarat",
        }
