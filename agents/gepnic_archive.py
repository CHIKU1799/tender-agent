"""
GePNIC Archive + Awards Agent
Scrapes past/closed tenders and Result-of-Tenders (awarded contracts)
using GPT-4o vision to bypass the CAPTCHA on GePNIC portals.

Modes:
  scope="archive"  — closed/past tenders (FrontEndTendersInArchive)
  scope="awards"   — awarded contracts (FrontEndResultOfTenders)
  scope="both"     — archive then awards in one run
"""
from __future__ import annotations
import asyncio
import logging
import random

from playwright.async_api import TimeoutError as PWTimeout

from agents.gepnic import GePNICAgent, EXTRACT_ROWS_JS, GET_PAGINATION_JS
from agents.base import ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig
from ai.captcha_solver import solve_and_submit

log = logging.getLogger("gepnic_archive")


class GePNICArchiveAgent(GePNICAgent):
    """
    Extends GePNICAgent to scrape archive and/or awards pages.
    Uses GPT-4o vision to bypass the CAPTCHA on these pages.
    """

    def __init__(self, config: PortalConfig, session: BrowserSession, scope: str = "both"):
        super().__init__(config, session)
        # scope: "archive" | "awards" | "both"
        self.scope = scope

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:
        """
        Scrape archive and/or awards based on self.scope.
        Falls back to active tenders if no archive/awards URL configured.
        """
        result = ScrapeResult(portal_id=self.portal_id)

        run_archive = self.scope in ("archive", "both")
        run_awards  = self.scope in ("awards",  "both")

        if run_archive:
            if not self.config.archive_url:
                log.warning(f"[{self.portal_id}] No archive_url configured — skipping archive")
            else:
                ar = await self._scrape_section(
                    url=self.config.archive_url,
                    status_label="Archive",
                    max_pages=max_pages,
                    org_filter=org_filter,
                    progress_cb=progress_cb,
                )
                result.tenders.extend(ar.tenders)
                result.pages += ar.pages
                result.errors.extend(ar.errors)

        if run_awards:
            if not self.config.awards_url:
                log.warning(f"[{self.portal_id}] No awards_url configured — skipping awards")
            else:
                aw = await self._scrape_section(
                    url=self.config.awards_url,
                    status_label="Awarded",
                    max_pages=max_pages,
                    org_filter=org_filter,
                    progress_cb=progress_cb,
                )
                result.tenders.extend(aw.tenders)
                result.pages += aw.pages
                result.errors.extend(aw.errors)

        if fetch_details and result.tenders:
            ctx = await self.session.new_context()
            try:
                result.tenders = await self._fetch_all_details(result.tenders, ctx)
            finally:
                await ctx.close()

        return result

    async def _scrape_section(
        self,
        url: str,
        status_label: str,
        max_pages: int | None,
        org_filter: str | None,
        progress_cb=None,
    ) -> ScrapeResult:
        """Navigate to url, solve CAPTCHA, paginate and collect tenders."""
        result = ScrapeResult(portal_id=self.portal_id)
        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            # 1. Seed session
            log.info(f"[{self.portal_id}/{status_label}] Seeding session...")
            await self._goto(page, self.config.session_seed_url)
            await random_delay(1.5, 3.0)

            # 2. Navigate to archive/awards page (CAPTCHA will appear)
            log.info(f"[{self.portal_id}/{status_label}] Loading {url}")
            await self._goto(page, url)
            await page.wait_for_timeout(2000)

            # 3. Solve CAPTCHA
            log.info(f"[{self.portal_id}/{status_label}] Solving CAPTCHA...")
            solved = await solve_and_submit(page, max_retries=3, submit=True)
            if not solved:
                msg = f"CAPTCHA solve failed for {status_label}"
                log.error(f"[{self.portal_id}] {msg}")
                result.errors.append(msg)
                return result

            await page.wait_for_timeout(2000)

            # 4. Paginate results
            current_page = 1
            empty_streak = 0

            while True:
                if max_pages and current_page > max_pages:
                    break

                rows = await page.evaluate(EXTRACT_ROWS_JS, self._js_cfg)

                if not rows:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                else:
                    empty_streak = 0
                    tenders = self._parse_rows_with_status(rows, current_page, status_label)
                    if org_filter:
                        tenders = [t for t in tenders if org_filter.lower() in t["organisation"].lower()]
                    result.tenders.extend(tenders)

                pagination = await page.evaluate(GET_PAGINATION_JS)
                ptext = pagination.get("paginationText") or f"Page {current_page}"
                log.info(f"[{self.portal_id}/{status_label}] {ptext} — {len(result.tenders)} total")

                result.pages = current_page

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                if not pagination["hasNext"]:
                    break

                await random_delay()
                await self.session.rotate_ua(ctx)

                if not await self._click_next(page):
                    break

                current_page += 1
                await page.wait_for_timeout(1500)

        except Exception as e:
            log.error(f"[{self.portal_id}/{status_label}] Error: {e}")
            result.errors.append(str(e))
        finally:
            await ctx.close()

        return result

    def _parse_rows_with_status(self, rows: list[dict], page_num: int, status: str) -> list[dict]:
        """Parse rows and stamp with the given status label."""
        tenders = self._parse_rows(rows, page_num)
        for t in tenders:
            t["status"] = status
        return tenders
