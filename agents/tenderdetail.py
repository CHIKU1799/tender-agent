"""
TenderDetail.com scraper — tenderdetail.com
Aggregator with active, closed and awarded tenders.
"""
from __future__ import annotations
import asyncio, logging, re, random
from datetime import datetime
from typing import Optional
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay, wait_for_content
from ai.captcha_advanced import solve_any_captcha

log = logging.getLogger("agents.tenderdetail")
BASE = "https://www.tenderdetail.com"


class TenderDetailAgent(BaseAgent):

    def __init__(self, cfg, session: BrowserSession, scope: str = "active"):
        super().__init__(cfg)
        self.session = session
        self.scope = scope

    async def scrape(self, max_pages=None, org_filter=None, fetch_details=False, progress_cb=None) -> ScrapeResult:
        result  = ScrapeResult(portal_id=self.config.portal_id)
        tenders = []
        scopes  = ["active","archive","awards"] if self.scope == "all" else \
                  ["archive","awards"] if self.scope == "both" else [self.scope]

        try:
            ctx  = await self.session.new_context(portal_id="tenderdetail")
            page = await self.session.new_page(ctx, portal_id="tenderdetail")
            await page.goto(BASE, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(1.5)

            for sc in scopes:
                url    = {"active":  "https://www.tenderdetail.com/viewalltender.aspx",
                          "archive": "https://www.tenderdetail.com/viewalltender.aspx?status=closed",
                          "awards":  "https://www.tenderdetail.com/viewalltender.aspx?status=awarded"}.get(sc, BASE)
                status = {"archive":"Archive","awards":"Awarded"}.get(sc,"Active")
                batch  = await self._scrape_pages(page, url, status, max_pages or 10, org_filter, progress_cb)
                tenders.extend(batch)

                if fetch_details and batch:
                    batch = await self._enrich(page, batch)

            await self.session.save_cookies(ctx, "tenderdetail")
            await ctx.close()
        except Exception as e:
            log.error(f"[tenderdetail] {e}")
            result.errors.append(str(e))

        result.tenders = tenders
        return result

    async def _scrape_pages(self, page, url, status, max_pages, org_filter, progress_cb):
        tenders  = []
        page_num = 0
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await wait_for_content(page)
        await solve_any_captcha(page)

        while page_num < max_pages:
            page_num += 1
            rows = await self._parse_table(page, status, org_filter)
            tenders.extend(rows)
            if progress_cb:
                await progress_cb(page_num, len(tenders))
            if not rows:
                break
            if not await self._next_page(page):
                break
            await asyncio.sleep(random.uniform(1.5, 3.0))
        return tenders

    async def _parse_table(self, page, status, org_filter):
        rows = []
        try:
            # TenderDetail uses a standard table
            table_rows = page.locator("table tbody tr, .tender-list tr")
            count = await table_rows.count()
            for i in range(count):
                cells = []
                for j in range(await table_rows.nth(i).locator("td").count()):
                    cells.append((await table_rows.nth(i).locator("td").nth(j).inner_text()).strip())
                if len(cells) < 3:
                    continue
                if org_filter and org_filter.lower() not in " ".join(cells).lower():
                    continue

                detail_url = ""
                try:
                    link = table_rows.nth(i).locator("a").first
                    if await link.count() > 0:
                        href = await link.get_attribute("href") or ""
                        detail_url = BASE + href if href.startswith("/") else href
                except Exception:
                    pass

                rows.append({
                    "portal_id":        self.config.portal_id,
                    "portal_name":      self.config.display_name,
                    "source_website":   BASE,
                    "tender_id":        cells[0] if cells else "",
                    "title":            cells[1] if len(cells)>1 else "",
                    "organisation":     cells[2] if len(cells)>2 else "",
                    "state":            cells[3] if len(cells)>3 else "",
                    "published_date":   cells[4] if len(cells)>4 else "",
                    "closing_date":     cells[5] if len(cells)>5 else "",
                    "tender_value_inr": cells[6] if len(cells)>6 else "",
                    "emd_inr":          cells[7] if len(cells)>7 else "",
                    "award_winner":     cells[8] if (status == "Awarded" and len(cells)>8) else "",
                    "award_date":       cells[9] if (status == "Awarded" and len(cells)>9) else "",
                    "award_amount":     cells[10] if (status == "Awarded" and len(cells)>10) else "",
                    "detail_url":       detail_url,
                    "status":           status,
                    "scraped_at":       datetime.utcnow().isoformat(),
                })
        except Exception as e:
            log.debug(f"[tenderdetail] parse: {e}")
        return rows

    async def _enrich(self, page, tenders):
        for t in tenders[:30]:
            if not t.get("detail_url"):
                continue
            try:
                await page.goto(t["detail_url"], wait_until="domcontentloaded", timeout=25_000)
                text = await page.inner_text("body")
                for field, pattern in [
                    ("emd_inr",          r"EMD[:\s]+([\d,\.]+)"),
                    ("tender_fee_inr",   r"(?:Tender|Document) Fee[:\s]+([\d,\.]+)"),
                    ("tender_value_inr", r"(?:Tender Value|Estimated Value|Amount)[:\s]+([\d,\.]+\s*(?:Cr|L|Lakh|crore)?)"),
                    ("tender_type",      r"Tender Type[:\s]+([^\n]+)"),
                    ("work_description", r"(?:Work|Scope)[:\s]+([^\n]{20,200})"),
                    ("contact",          r"Contact[:\s]+([^\n]+)"),
                    ("location",         r"Location[:\s]+([^\n]+)"),
                    ("award_winner",     r"(?:Awarded To|Winner|Contractor|Successful Bidder)[:\s]+([^\n]+)"),
                    ("award_date",       r"(?:Award Date|AOC Date|Date of Award)[:\s]+([^\n]+)"),
                    ("award_amount",     r"(?:Award Amount|Contract Value|Awarded Value|Contract Amount)[:\s]+([\d,\.]+)"),
                ]:
                    if not t.get(field):
                        m = re.search(pattern, text, re.IGNORECASE)
                        if m:
                            t[field] = m.group(1).strip()
                t["detail_scraped"] = "true"
                await random_delay(1.0, 2.0)
            except Exception:
                pass
        return tenders

    async def _next_page(self, page):
        for sel in ["a:has-text('Next')", ".next a", "a:has-text('>')", "[aria-label='Next']"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    cls = await el.get_attribute("class") or ""
                    if "disabled" in cls:
                        return False
                    await el.click()
                    await wait_for_content(page, timeout=20_000)
                    return True
            except Exception:
                continue
        return False
