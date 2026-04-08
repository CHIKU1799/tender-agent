"""
Palladium PrimeNumbers scraper — app.palladium.primenumbers.in
Modern React/SPA portal — intercepts API calls.
"""
from __future__ import annotations
import asyncio, logging, json, re
from datetime import datetime
from typing import Optional
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, wait_for_content, random_delay

log = logging.getLogger("agents.palladium")
BASE = "https://app.palladium.primenumbers.in"


class PalladiumAgent(BaseAgent):

    def __init__(self, cfg, session: BrowserSession, scope: str = "active"):
        super().__init__(cfg)
        self.session = session
        self.scope = scope

    async def scrape(self, max_pages=None, org_filter=None, fetch_details=False, progress_cb=None) -> ScrapeResult:
        result  = ScrapeResult(portal_id=self.config.portal_id)
        tenders = []

        try:
            ctx  = await self.session.new_context(portal_id="palladium")
            page = await self.session.new_page(ctx, portal_id="palladium")

            # Intercept API responses — SPA portals call JSON APIs
            api_items: list[dict] = []

            async def capture(response):
                if response.status == 200:
                    ct = response.headers.get("content-type","")
                    if "json" in ct:
                        try:
                            data = await response.json()
                            if isinstance(data, list):
                                api_items.extend(data)
                            elif isinstance(data, dict):
                                for key in ("data","tenders","bids","results","items","records","content"):
                                    if isinstance(data.get(key), list):
                                        api_items.extend(data[key])
                                        break
                        except Exception:
                            pass

            page.on("response", capture)

            await page.goto(BASE, wait_until="domcontentloaded", timeout=45_000)
            await wait_for_content(page)
            await asyncio.sleep(3)

            # Scroll to trigger lazy loads
            for _ in range(5):
                await page.mouse.wheel(0, 800)
                await asyncio.sleep(1)

            # Try clicking Tenders/Bids menu if present
            for nav_sel in ["a:has-text('Tender')", "a:has-text('Bid')", "button:has-text('Tender')",
                             "[href*='tender']", "[href*='bid']"]:
                try:
                    el = page.locator(nav_sel).first
                    if await el.count() > 0:
                        await el.click()
                        await wait_for_content(page)
                        await asyncio.sleep(2)
                        for _ in range(3):
                            await page.mouse.wheel(0, 800)
                            await asyncio.sleep(0.8)
                        break
                except Exception:
                    continue

            if progress_cb:
                await progress_cb(1, len(api_items))

            # Normalise API items
            for item in api_items:
                t = self._normalise(item)
                if t:
                    tenders.append(t)

            # Fallback — scrape visible table if no API data
            if not tenders:
                tenders = await self._scrape_table(page)

            await self.session.save_cookies(ctx, "palladium")
            await ctx.close()

        except Exception as e:
            log.error(f"[palladium] {e}")
            result.errors.append(str(e))

        log.info(f"[palladium] {len(tenders)} tenders")
        result.tenders = tenders
        return result

    def _normalise(self, item: dict) -> Optional[dict]:
        if not item or not isinstance(item, dict):
            return None

        def g(*keys):
            for k in keys:
                for v in [item.get(k), item.get(k.lower()), item.get(k.upper())]:
                    if v:
                        return str(v).strip()
            return ""

        title = g("title","name","tenderTitle","bidTitle","subject","workTitle")
        if not title:
            return None

        return {
            "portal_id":        self.config.portal_id,
            "portal_name":      self.config.display_name,
            "source_website":   BASE,
            "tender_id":        g("id","tenderId","bidId","referenceNo","ref_no"),
            "ref_number":       g("referenceNo","refNo","reference"),
            "title":            title,
            "organisation":     g("organisation","department","buyer","ministry","org"),
            "state":            g("state","location","region"),
            "published_date":   g("publishedDate","startDate","createdAt","issueDate"),
            "closing_date":     g("closingDate","endDate","dueDate","bidEndDate","deadline"),
            "tender_value_inr": g("value","amount","estimatedValue","tenderValue","bidValue"),
            "emd_inr":          g("emd","earnestMoney","securityDeposit"),
            "tender_fee_inr":   g("tenderFee","documentFee","bidFee"),
            "tender_type":      g("type","tenderType","category"),
            "award_winner":     g("awardedTo","winner","vendor","contractor","successfulBidder"),
            "award_date":       g("awardDate","aocDate","dateOfAward"),
            "award_amount":     g("awardAmount","contractValue","awardedValue"),
            "status":           g("status","tenderStatus","bidStatus") or "Active",
            "detail_url":       g("url","link","detailUrl","detailLink"),
            "scraped_at":       datetime.utcnow().isoformat(),
        }

    async def _scrape_table(self, page) -> list[dict]:
        """Fallback HTML table scrape."""
        rows = []
        try:
            table_rows = page.locator("table tbody tr")
            count = await table_rows.count()
            for i in range(count):
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
                        detail_url = href if href.startswith("http") else BASE + href
                except Exception:
                    pass
                rows.append({
                    "portal_id":    self.config.portal_id,
                    "portal_name":  self.config.display_name,
                    "source_website": BASE,
                    "tender_id":    cells[0],
                    "title":        cells[1] if len(cells)>1 else "",
                    "organisation": cells[2] if len(cells)>2 else "",
                    "closing_date": cells[3] if len(cells)>3 else "",
                    "status":       "Active",
                    "detail_url":   detail_url,
                    "scraped_at":   datetime.utcnow().isoformat(),
                })
        except Exception as e:
            log.debug(f"[palladium] table: {e}")
        return rows
