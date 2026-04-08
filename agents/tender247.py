"""
Tender247 Scraper — tender247.com
Largest Indian tender aggregator — active, archive, awarded.
Scrapes via paginated listing + detail pages.
"""
from __future__ import annotations
import asyncio
import logging
import re
import random
from datetime import datetime
from typing import Optional

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay, wait_for_content, human_scroll
from ai.captcha_advanced import solve_any_captcha

log = logging.getLogger("agents.tender247")

BASE   = "https://www.tender247.com"
# Multiple URL patterns — tender247 changes URL structure frequently
SEARCH_PATTERNS = [
    "https://www.tender247.com/keyword/+/0/0/0/0/0/0/0/{page}",
    "https://www.tender247.com/tenders?page={page}",
    "https://www.tender247.com/tenders/{page}",
]
CLOSED_PATTERNS = [
    "https://www.tender247.com/closed-tenders/{page}",
    "https://www.tender247.com/tenders?status=closed&page={page}",
    "https://www.tender247.com/archive-tenders/{page}",
    "https://www.tender247.com/tenders?type=archive&page={page}",
]
AWARDS_PATTERNS = [
    "https://www.tender247.com/awarded-tenders/{page}",
    "https://www.tender247.com/tender-results/{page}",
    "https://www.tender247.com/tenders?status=awarded&page={page}",
    "https://www.tender247.com/tenders?type=result&page={page}",
]

# State code map for filtering
STATE_CODES = {
    "andhra pradesh": "1", "arunachal pradesh": "2", "assam": "3",
    "bihar": "4", "chhattisgarh": "5", "goa": "6", "gujarat": "7",
    "haryana": "8", "himachal pradesh": "9", "jharkhand": "10",
    "karnataka": "11", "kerala": "12", "madhya pradesh": "13",
    "maharashtra": "14", "manipur": "15", "meghalaya": "16",
    "mizoram": "17", "nagaland": "18", "odisha": "19", "punjab": "20",
    "rajasthan": "21", "sikkim": "22", "tamil nadu": "23",
    "telangana": "24", "tripura": "25", "uttar pradesh": "26",
    "uttarakhand": "27", "west bengal": "28", "delhi": "29",
}


class Tender247Agent(BaseAgent):

    def __init__(self, cfg, session: BrowserSession, scope: str = "active"):
        super().__init__(cfg)
        self.session = session
        self.scope = scope

    async def scrape(
        self,
        max_pages: Optional[int] = None,
        org_filter: Optional[str] = None,
        fetch_details: bool = False,
        progress_cb=None,
        state_filter: Optional[str] = None,
    ) -> ScrapeResult:

        result  = ScrapeResult(portal_id=self.config.portal_id)
        tenders = []

        scopes = ["active", "archive", "awards"] if self.scope in ("both", "all") else \
                 ["archive", "awards"] if self.scope == "both" else [self.scope]

        try:
            ctx  = await self.session.new_context(portal_id="tender247")
            page = await self.session.new_page(ctx, portal_id="tender247")

            # Warm up
            await page.goto(BASE, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

            for sc in scopes:
                log.info(f"[tender247] scope={sc}")
                batch = await self._scrape_scope(page, sc, max_pages or 10, progress_cb)
                tenders.extend(batch)
                log.info(f"[tender247] {sc}: {len(batch)} tenders")
                await asyncio.sleep(random.uniform(2, 4))

            # Fetch details if requested
            if fetch_details and tenders:
                tenders = await self._fetch_all_details(page, tenders)

            await self.session.save_cookies(ctx, "tender247")
            await ctx.close()

        except Exception as e:
            log.error(f"[tender247] {e}")
            result.errors.append(str(e))

        result.tenders = tenders
        return result

    async def _scrape_scope(self, page, scope, max_pages, progress_cb):
        tenders  = []
        status = {"active": "Active", "archive": "Archive", "awards": "Awarded"}.get(scope, "Active")

        url_patterns = {
            "active":  SEARCH_PATTERNS,
            "archive": CLOSED_PATTERNS,
            "awards":  AWARDS_PATTERNS,
        }.get(scope, SEARCH_PATTERNS)

        # Try each URL pattern until one works
        working_pattern = None
        for pattern in url_patterns:
            try:
                test_url = pattern.format(page=1)
                await page.goto(test_url, wait_until="domcontentloaded", timeout=30_000)
                await wait_for_content(page)
                await solve_any_captcha(page)
                await human_scroll(page, 2)

                rows = await self._parse_listing(page, status)
                if rows:
                    tenders.extend(rows)
                    working_pattern = pattern
                    log.info(f"[tender247] {scope}: pattern {pattern} works — {len(rows)} rows")
                    break
                else:
                    log.debug(f"[tender247] {scope}: pattern {pattern} returned 0 rows, trying next")
            except Exception as e:
                log.debug(f"[tender247] {scope}: pattern {pattern} failed: {e}")
                continue

        if not working_pattern:
            # Last resort: navigate to main page + click tabs/links for scope
            tenders = await self._try_navigation_fallback(page, scope, status)
            if progress_cb and tenders:
                await progress_cb(1, len(tenders))
            return tenders

        # Continue paginating with the working pattern
        if progress_cb:
            await progress_cb(1, len(tenders))

        page_num = 1
        while page_num < max_pages:
            page_num += 1
            url = working_pattern.format(page=page_num)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                await wait_for_content(page)
                await solve_any_captcha(page)
                await human_scroll(page, 2)

                rows = await self._parse_listing(page, status)
                if not rows:
                    break

                tenders.extend(rows)
                if progress_cb:
                    await progress_cb(page_num, len(tenders))

                await random_delay(2.0, 4.0)

            except Exception as e:
                log.warning(f"[tender247] page {page_num}: {e}")
                break

        return tenders

    async def _try_navigation_fallback(self, page, scope, status):
        """Navigate to main page and click tabs/links to reach the right scope."""
        tenders = []
        try:
            await page.goto(BASE + "/tenders", wait_until="domcontentloaded", timeout=30_000)
            await wait_for_content(page)

            # Try clicking archive/awarded tab or link
            tab_keywords = {
                "archive": ["archive", "closed", "past", "expired"],
                "awards":  ["awarded", "result", "winner", "completed"],
                "active":  ["active", "live", "fresh", "current", "open"],
            }
            for keyword in tab_keywords.get(scope, []):
                for sel in [f"a:has-text('{keyword}')", f"button:has-text('{keyword}')",
                            f"[class*='{keyword}']", f"li:has-text('{keyword}') a",
                            f"[data-tab*='{keyword}']", f"[href*='{keyword}']"]:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            await el.click()
                            await wait_for_content(page)
                            await asyncio.sleep(2)
                            rows = await self._parse_listing(page, status)
                            if rows:
                                tenders.extend(rows)
                                log.info(f"[tender247] fallback nav {keyword}: {len(rows)} rows")
                                return tenders
                    except Exception:
                        continue
        except Exception as e:
            log.debug(f"[tender247] fallback: {e}")
        return tenders

    async def _parse_listing(self, page, status: str) -> list[dict]:
        rows = []
        try:
            # Tender247 uses table or card-based layout
            selectors = [
                "table.tender-table tbody tr",
                ".tender-list-item", ".tender-row",
                "table tbody tr", ".list-group-item",
            ]

            for sel in selectors:
                items = page.locator(sel)
                count = await items.count()
                if count < 1:
                    continue

                for i in range(count):
                    item = items.nth(i)
                    try:
                        # Extract all text
                        text = await item.inner_text()
                        lines = [l.strip() for l in text.split("\n") if l.strip()]
                        if len(lines) < 2:
                            continue

                        # Get detail URL
                        detail_url = ""
                        try:
                            link = item.locator("a").first
                            if await link.count() > 0:
                                href = await link.get_attribute("href") or ""
                                detail_url = BASE + href if href.startswith("/") else href
                        except Exception:
                            pass

                        # Extract fields from text
                        title = lines[0] if lines else ""
                        org   = ""
                        pub_date = closing_date = value = state = ref = ""
                        emd = award_winner = award_date = award_amount = tender_fee = ""

                        for line in lines[1:]:
                            ll = line.lower()
                            if "organisation" in ll or "dept" in ll or "ministry" in ll:
                                org = line.split(":")[-1].strip()
                            elif "closing" in ll or "due date" in ll or "bid end" in ll:
                                closing_date = re.sub(r"[^0-9/\-]", "", line.split(":")[-1]).strip()
                            elif "published" in ll or "start date" in ll or "tender date" in ll:
                                pub_date = re.sub(r"[^0-9/\-]", "", line.split(":")[-1]).strip()
                            elif "value" in ll or "amount" in ll or "₹" in line or "rs." in ll:
                                value = line.split(":")[-1].strip()
                            elif "state" in ll or "location" in ll:
                                state = line.split(":")[-1].strip()
                            elif "ref" in ll or "tender no" in ll or "nit" in ll:
                                ref = line.split(":")[-1].strip()
                            elif "emd" in ll or "earnest" in ll:
                                emd = line.split(":")[-1].strip()
                            elif "awarded to" in ll or "winner" in ll or "contractor" in ll:
                                award_winner = line.split(":")[-1].strip()
                            elif "award date" in ll or "aoc date" in ll:
                                award_date = line.split(":")[-1].strip()
                            elif "award amount" in ll or "contract value" in ll:
                                award_amount = line.split(":")[-1].strip()
                            elif "tender fee" in ll or "document fee" in ll:
                                tender_fee = line.split(":")[-1].strip()

                        rows.append({
                            "portal_id":        "tender247",
                            "portal_name":      "Tender247",
                            "source_website":   BASE,
                            "tender_id":        ref or detail_url.split("/")[-1],
                            "ref_number":       ref,
                            "title":            title,
                            "organisation":     org,
                            "state":            state,
                            "published_date":   pub_date,
                            "closing_date":     closing_date,
                            "tender_value_inr": value,
                            "emd_inr":          emd,
                            "tender_fee_inr":   tender_fee,
                            "award_winner":     award_winner,
                            "award_date":       award_date,
                            "award_amount":     award_amount,
                            "detail_url":       detail_url,
                            "status":           status,
                            "scraped_at":       datetime.utcnow().isoformat(),
                        })
                    except Exception:
                        continue

                if rows:
                    break

        except Exception as e:
            log.debug(f"[tender247] parse: {e}")

        return rows

    async def _fetch_all_details(self, page, tenders: list[dict]) -> list[dict]:
        """Fetch detail page for each tender to get complete information."""
        enriched = []
        for i, t in enumerate(tenders):
            if not t.get("detail_url"):
                enriched.append(t)
                continue
            try:
                details = await self._fetch_detail(page, t["detail_url"])
                t.update(details)
                t["detail_scraped"] = "true"
                log.debug(f"[tender247] detail {i+1}/{len(tenders)}: {t['title'][:40]}")
                await random_delay(1.5, 3.0)
            except Exception as e:
                log.debug(f"[tender247] detail fetch failed: {e}")
            enriched.append(t)
        return enriched

    async def _fetch_detail(self, page, url: str) -> dict:
        """Scrape a tender detail page and return enriched fields."""
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await wait_for_content(page)

        details = {}
        try:
            text = await page.inner_text("body")

            # Parse key-value pairs from detail page
            patterns = {
                "tender_id":         r"(?:Tender No|Tender ID|NIT No)[:\s]+([^\n]+)",
                "ref_number":        r"(?:Ref No|Reference)[:\s]+([^\n]+)",
                "organisation":      r"(?:Organisation|Department|Ministry)[:\s]+([^\n]+)",
                "tender_value_inr":  r"(?:Tender Value|Estimated Value|Amount)[:\s]+([\d,\.]+\s*(?:Cr|L|Lakh|crore)?)",
                "emd_inr":           r"(?:EMD|Earnest Money)[:\s]+([\d,\.]+)",
                "tender_fee_inr":    r"(?:Tender Fee|Document Fee)[:\s]+([\d,\.]+)",
                "closing_date":      r"(?:Closing Date|Bid End Date|Last Date)[:\s]+([^\n]+)",
                "published_date":    r"(?:Published|Start Date|Tender Date)[:\s]+([^\n]+)",
                "bid_submission_end": r"(?:Bid Submission.*?End|Submit.*?Before)[:\s]+([^\n]+)",
                "pre_bid_meeting":   r"(?:Pre.*?Bid Meeting|Pre-bid)[:\s]+([^\n]+)",
                "location":          r"(?:Location|Place|Work Location)[:\s]+([^\n]+)",
                "pincode":           r"(?:PIN|Pincode)[:\s]+(\d{6})",
                "contact":           r"(?:Contact|Phone|Email)[:\s]+([^\n]+)",
                "tender_type":       r"(?:Tender Type|Work Type)[:\s]+([^\n]+)",
                "tender_category":   r"(?:Category|Work Category)[:\s]+([^\n]+)",
                "work_description":  r"(?:Work Description|Scope)[:\s]+([^\n]{20,200})",
                "award_winner":      r"(?:Awarded To|Winner|Contractor)[:\s]+([^\n]+)",
                "award_date":        r"(?:Award Date|AOC Date)[:\s]+([^\n]+)",
                "award_amount":      r"(?:Award Amount|Contract Value)[:\s]+([\d,\.]+)",
            }

            for field, pattern in patterns.items():
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    details[field] = m.group(1).strip()

        except Exception as e:
            log.debug(f"[tender247] detail parse: {e}")

        return details
