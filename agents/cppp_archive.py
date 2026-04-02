"""
CPPP Archive Scraper — eprocure.gov.in
Scrapes closed/past tenders and awarded contracts.
CPPP has no CAPTCHA on archive pages — just pagination.
"""
from __future__ import annotations
import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay, wait_for_content

log = logging.getLogger("agents.cppp_archive")

# CPPP archive search URLs
ARCHIVE_URL = "https://eprocure.gov.in/eprocure/app?component=BasicSearchTender&page=BasicSearchTender&service=page"
AWARDS_URL  = "https://eprocure.gov.in/eprocure/app?component=AwardedTender&page=AwardedTender&service=page"


class CPPPArchiveAgent(BaseAgent):

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

        result   = ScrapeResult(portal_id=self.config.portal_id)
        tenders  = []
        scopes   = ["archive", "awards"] if self.scope == "both" else [self.scope]

        try:
            ctx  = await self.session.new_context(portal_id=self.config.portal_id)
            page = await self.session.new_page(ctx, portal_id=self.config.portal_id)

            for sc in scopes:
                url    = AWARDS_URL if sc == "awards" else ARCHIVE_URL
                status = "Awarded"  if sc == "awards" else "Archive"
                batch  = await self._scrape_paginated(page, url, status, max_pages, org_filter, progress_cb)
                tenders.extend(batch)
                log.info(f"[cppp-archive] {sc}: {len(batch)} tenders")

            await ctx.close()

        except Exception as e:
            log.error(f"[cppp-archive] {e}")
            result.errors.append(str(e))

        result.tenders = tenders
        return result

    async def _scrape_paginated(self, page, url, status, max_pages, org_filter, progress_cb):
        tenders  = []
        page_num = 0
        max_p    = max_pages or 100  # CPPP is fast, allow more pages

        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await wait_for_content(page)

        # Fill org filter if provided
        if org_filter:
            try:
                org_input = page.locator("input[name='organame'], input[name='organisation'], #organame").first
                if await org_input.count() > 0:
                    await org_input.fill(org_filter)
                    await page.keyboard.press("Enter")
                    await wait_for_content(page)
            except Exception:
                pass

        while page_num < max_p:
            page_num += 1
            rows = await self._parse_table(page, status)
            tenders.extend(rows)

            if progress_cb:
                await progress_cb(page_num, len(tenders))

            if not rows:
                break

            # Next page
            went = await self._go_next(page)
            if not went:
                break

            await asyncio.sleep(random.uniform(1.5, 3.0))

        return tenders

    async def _parse_table(self, page, status):
        rows = []
        try:
            table_rows = page.locator("table.list_table tbody tr, table#tenderBasicList tbody tr, table tbody tr")
            count = await table_rows.count()

            for i in range(count):
                cells = []
                cell_els = table_rows.nth(i).locator("td")
                for j in range(await cell_els.count()):
                    cells.append((await cell_els.nth(j).inner_text()).strip())

                if len(cells) < 4:
                    continue

                detail_url = ""
                try:
                    link = table_rows.nth(i).locator("a").first
                    if await link.count() > 0:
                        href = await link.get_attribute("href") or ""
                        if href and "javascript" not in href:
                            detail_url = "https://eprocure.gov.in" + href if href.startswith("/") else href
                except Exception:
                    pass

                rows.append({
                    "portal_id":        self.config.portal_id,
                    "portal_name":      self.config.display_name,
                    "source_website":   "https://eprocure.gov.in",
                    "tender_id":        cells[1] if len(cells) > 1 else "",
                    "title":            cells[2] if len(cells) > 2 else cells[0],
                    "organisation":     cells[3] if len(cells) > 3 else "",
                    "published_date":   cells[4] if len(cells) > 4 else "",
                    "closing_date":     cells[5] if len(cells) > 5 else "",
                    "tender_value_inr": cells[6] if len(cells) > 6 else "",
                    "emd_inr":          cells[7] if len(cells) > 7 else "",
                    "award_winner":     cells[8] if (status == "Awarded" and len(cells) > 8) else "",
                    "award_date":       cells[9] if (status == "Awarded" and len(cells) > 9) else "",
                    "award_amount":     cells[10] if (status == "Awarded" and len(cells) > 10) else "",
                    "status":           status,
                    "detail_url":       detail_url,
                    "scraped_at":       datetime.utcnow().isoformat(),
                })
        except Exception as e:
            log.debug(f"[cppp-archive] table parse: {e}")
        return rows

    async def _go_next(self, page):
        for sel in ["a:has-text('Next')", "a:has-text('>')", ".next a", "a[title='Next']"]:
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
