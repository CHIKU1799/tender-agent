"""
GeM Archive Scraper — gem.gov.in
Scrapes past/closed bids and awarded orders via GeM API.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiohttp

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession

log = logging.getLogger("agents.gem_archive")

GEM_BID_API    = "https://bidplus.gem.gov.in/bidlists"
GEM_ORDERS_API = "https://bidplus.gem.gov.in/all-bids"


class GeMArchiveAgent(BaseAgent):

    def __init__(self, cfg, session: BrowserSession, scope: str = "archive"):
        super().__init__(cfg)
        self.session = session
        self.scope = scope

    async def scrape(
        self,
        max_pages: Optional[int] = None,
        org_filter: Optional[str] = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result  = ScrapeResult(portal_id=self.config.portal_id)
        tenders = []

        try:
            ctx  = await self.session.new_context(portal_id=self.config.portal_id)
            page = await self.session.new_page(ctx, portal_id=self.config.portal_id)

            # GeM — intercept API calls
            api_items: list[dict] = []

            async def capture(response):
                if response.status == 200 and "bidlists" in response.url:
                    try:
                        data = await response.json()
                        items = data if isinstance(data, list) else data.get("data", [])
                        api_items.extend(items)
                    except Exception:
                        pass

            page.on("response", capture)

            # Navigate to past bids
            scopes = ["archive", "awards"] if self.scope == "both" else [self.scope]

            for sc in scopes:
                status = "Awarded" if sc == "awards" else "Archive"
                # GeM uses status filters in URL
                url = f"https://bidplus.gem.gov.in/bidlists?bid_status={'2' if sc=='awards' else '3'}"
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                await asyncio.sleep(3)

                # Scroll to trigger more API calls
                for _ in range(min(max_pages or 5, 10)):
                    await page.mouse.wheel(0, 800)
                    await asyncio.sleep(1.5)

                if progress_cb:
                    await progress_cb(1, len(api_items))

            # Normalise captured items
            for item in api_items:
                t = self._normalise(item)
                if t:
                    tenders.append(t)

            await ctx.close()

        except Exception as e:
            log.error(f"[gem-archive] {e}")
            result.errors.append(str(e))

        result.tenders = tenders
        return result

    def _normalise(self, item: dict) -> Optional[dict]:
        def g(*keys):
            for k in keys:
                v = item.get(k)
                if v:
                    return str(v).strip()
            return ""

        status = "Awarded" if g("bid_status") in ("2", "awarded") else "Archive"

        return {
            "portal_id":      self.config.portal_id,
            "portal_name":    self.config.display_name,
            "tender_id":      g("bid_number", "bidNumber", "id"),
            "title":          g("bid_title",  "bidTitle",  "item_name"),
            "organisation":   g("ministry",   "department","buyer_org"),
            "published_date": g("bid_start_date", "startDate"),
            "closing_date":   g("bid_end_date",   "endDate"),
            "tender_value_inr": g("estimated_bid_value", "totalValue"),
            "gem_category":   g("category", "itemCategory"),
            "status":         status,
            "detail_url":     f"https://bidplus.gem.gov.in/showbidDocument/{g('bid_number')}",
            "scraped_at":     datetime.utcnow().isoformat(),
        }
