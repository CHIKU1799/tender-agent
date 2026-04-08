"""
TenderTiger scraper — tendertiger.com
Handles active, closed and awarded tenders.
"""
from __future__ import annotations
import asyncio, logging, re, random
from datetime import datetime
from typing import Optional
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay, wait_for_content, human_scroll
from ai.captcha_advanced import solve_any_captcha

log = logging.getLogger("agents.tendertiger")
BASE = "https://www.tendertiger.com"

# TenderTiger URL patterns — they change structure, try multiple
ACTIVE_URLS = [
    f"{BASE}/tender/tenders.aspx",
    f"{BASE}/tenders",
    f"{BASE}/Home/AdvanceSearch",
]
ARCHIVE_URLS = [
    f"{BASE}/tender/closed-tenders.aspx",
    f"{BASE}/closed-tenders",
    f"{BASE}/tenders?status=closed",
    "https://www.tendertiger.co.in/Home/AdvanceSearch",
]
AWARDS_URLS = [
    f"{BASE}/tender/awarded-tenders.aspx",
    f"{BASE}/awarded-tenders",
    f"{BASE}/tender-results",
    f"{BASE}/tenders?status=awarded",
]


class TenderTigerAgent(BaseAgent):

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
            ctx  = await self.session.new_context(portal_id="tendertiger")
            page = await self.session.new_page(ctx, portal_id="tendertiger")

            # Warm up
            await page.goto(BASE, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

            for sc in scopes:
                url_list = {"active": ACTIVE_URLS, "archive": ARCHIVE_URLS, "awards": AWARDS_URLS}.get(sc, ACTIVE_URLS)
                status = {"archive":"Archive","awards":"Awarded"}.get(sc,"Active")

                batch = []
                for url in url_list:
                    batch = await self._scrape_pages(page, url, status, max_pages or 10, org_filter, progress_cb)
                    if batch:
                        log.info(f"[tendertiger] {sc}: URL {url} worked — {len(batch)} rows")
                        break
                    log.debug(f"[tendertiger] {sc}: URL {url} returned 0, trying next")

                # Fallback: navigate to main page and click tabs
                if not batch:
                    batch = await self._try_tab_navigation(page, sc, status)

                # Enrich details for awarded/archive tenders
                if fetch_details and batch:
                    batch = await self._enrich(page, batch)

                tenders.extend(batch)
                log.info(f"[tendertiger] {sc}: {len(batch)} tenders")

            await self.session.save_cookies(ctx, "tendertiger")
            await ctx.close()
        except Exception as e:
            log.error(f"[tendertiger] {e}")
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
            await human_scroll(page, 2)
            rows = await self._parse_page(page, status, org_filter)
            tenders.extend(rows)
            if progress_cb:
                await progress_cb(page_num, len(tenders))
            if not rows:
                break
            if not await self._next_page(page, page_num):
                break
            await asyncio.sleep(random.uniform(2.0, 4.0))
        return tenders

    async def _parse_page(self, page, status, org_filter):
        rows = []
        try:
            # TenderTiger uses table rows with class tender-row or similar
            selectors = ["table.table tbody tr", ".tenders-list tr", "table tbody tr"]
            for sel in selectors:
                els = page.locator(sel)
                count = await els.count()
                if count < 2:
                    continue
                for i in range(count):
                    cells = []
                    tds = els.nth(i).locator("td")
                    for j in range(await tds.count()):
                        cells.append((await tds.nth(j).inner_text()).strip())
                    if len(cells) < 3:
                        continue
                    full = " ".join(cells).lower()
                    if org_filter and org_filter.lower() not in full:
                        continue

                    detail_url = ""
                    try:
                        link = els.nth(i).locator("a").first
                        if await link.count() > 0:
                            href = await link.get_attribute("href") or ""
                            detail_url = href if href.startswith("http") else BASE + "/" + href.lstrip("/")
                    except Exception:
                        pass

                    rows.append({
                        "portal_id":        self.config.portal_id,
                        "portal_name":      self.config.display_name,
                        "source_website":   BASE,
                        "tender_id":        cells[0],
                        "title":            cells[1] if len(cells)>1 else "",
                        "organisation":     cells[2] if len(cells)>2 else "",
                        "state":            cells[3] if len(cells)>3 else "",
                        "closing_date":     cells[4] if len(cells)>4 else "",
                        "tender_value_inr": cells[5] if len(cells)>5 else "",
                        "emd_inr":          cells[6] if len(cells)>6 else "",
                        "award_winner":     cells[7] if (status == "Awarded" and len(cells)>7) else "",
                        "award_date":       cells[8] if (status == "Awarded" and len(cells)>8) else "",
                        "award_amount":     cells[9] if (status == "Awarded" and len(cells)>9) else "",
                        "detail_url":       detail_url,
                        "status":           status,
                        "scraped_at":       datetime.utcnow().isoformat(),
                    })
                if rows:
                    break
        except Exception as e:
            log.debug(f"[tendertiger] parse: {e}")
        return rows

    async def _try_tab_navigation(self, page, scope, status):
        """Fallback: go to homepage and click relevant tab/link."""
        rows = []
        try:
            await page.goto(BASE, wait_until="domcontentloaded", timeout=30_000)
            await wait_for_content(page)
            await asyncio.sleep(2)

            keywords = {
                "archive": ["closed", "archive", "past", "expired"],
                "awards":  ["awarded", "result", "winner", "completed"],
                "active":  ["active", "live", "open", "current"],
            }
            for keyword in keywords.get(scope, []):
                for sel in [f"a:has-text('{keyword}')", f"button:has-text('{keyword}')",
                            f"[href*='{keyword}']", f"nav a:has-text('{keyword}')"]:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            await el.click()
                            await wait_for_content(page)
                            await asyncio.sleep(2)
                            await human_scroll(page, 2)
                            rows = await self._parse_page(page, status, None)
                            if rows:
                                log.info(f"[tendertiger] tab nav '{keyword}': {len(rows)} rows")
                                return rows
                    except Exception:
                        continue
        except Exception as e:
            log.debug(f"[tendertiger] tab nav: {e}")
        return rows

    async def _enrich(self, page, tenders):
        """Visit detail pages to extract pricing, EMD, awardee data."""
        for t in tenders[:30]:
            if not t.get("detail_url"):
                continue
            try:
                await page.goto(t["detail_url"], wait_until="domcontentloaded", timeout=25_000)
                text = await page.inner_text("body")
                for field, pattern in [
                    ("tender_value_inr", r"(?:Tender Value|Estimated Value|Amount)[:\s]+([\d,\.]+\s*(?:Cr|L|Lakh|crore)?)"),
                    ("emd_inr",          r"(?:EMD|Earnest Money)[:\s]+([\d,\.]+)"),
                    ("tender_fee_inr",   r"(?:Tender Fee|Document Fee|Bid Fee)[:\s]+([\d,\.]+)"),
                    ("tender_type",      r"(?:Tender Type|Work Type)[:\s]+([^\n]+)"),
                    ("work_description", r"(?:Work Description|Scope|Brief)[:\s]+([^\n]{20,200})"),
                    ("location",         r"(?:Location|Place|Work Location)[:\s]+([^\n]+)"),
                    ("award_winner",     r"(?:Awarded To|Winner|Contractor|Successful Bidder)[:\s]+([^\n]+)"),
                    ("award_date",       r"(?:Award Date|AOC Date|Date of Award)[:\s]+([^\n]+)"),
                    ("award_amount",     r"(?:Award Amount|Contract Value|Awarded Value)[:\s]+([\d,\.]+)"),
                    ("contact",          r"(?:Contact|Phone|Email)[:\s]+([^\n]+)"),
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

    async def _next_page(self, page, current_page):
        for sel in [f"a:has-text('{current_page+1}')", "a:has-text('Next')", ".next a"]:
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
