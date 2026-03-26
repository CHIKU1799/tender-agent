"""
GeM Agent — Government e-Marketplace (bidplus.gem.gov.in)
Uses Playwright to load the all-bids page and intercepts the JSON API response.
API endpoint: https://bidplus.gem.gov.in/all-bids-data (requires browser session cookies)
"""
from __future__ import annotations
import asyncio
import json
import logging
from core.utils import now_iso
from core.browser import BrowserSession
from agents.base import BaseAgent, ScrapeResult
from portals.configs import PortalConfig

log = logging.getLogger("gem")

GEM_ALL_BIDS_URL  = "https://bidplus.gem.gov.in/all-bids"
GEM_ALL_BIDS_DATA = "https://bidplus.gem.gov.in/all-bids-data"


class GeMAgent(BaseAgent):

    def __init__(self, config: PortalConfig, session: BrowserSession | None = None):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id="gem")

        if self.session is None:
            from core.browser import BrowserSession as BS
            self.session = BS()
            await self.session.start()
            own_session = True
        else:
            own_session = False

        try:
            ctx  = await self.session.new_context()
            page = await self.session.new_page(ctx)

            page_num   = 1
            page_size  = 20       # GeM default

            try:
                # Set up route interception for all-bids-data BEFORE navigation
                captured_pages: dict[int, dict] = {}

                async def intercept_data(route, request):
                    resp = await route.fetch()
                    body = await resp.json()
                    # Extract page number from URL query
                    import urllib.parse as up
                    qs   = up.parse_qs(up.urlparse(request.url).query)
                    pno  = int((qs.get("pageNo") or ["1"])[0])
                    captured_pages[pno] = body
                    await route.fulfill(response=resp)

                await page.route(f"{GEM_ALL_BIDS_DATA}**", intercept_data)

                # Navigate — first page data intercepted automatically
                await page.goto(GEM_ALL_BIDS_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(4000)

                while True:
                    if max_pages and page_num > max_pages:
                        break

                    if page_num not in captured_pages:
                        log.warning(f"[gem] Page {page_num} not captured")
                        break

                    data = captured_pages[page_num]
                    docs = (data.get("response") or {}).get("response", {}).get("docs", [])

                    if not docs:
                        log.info(f"[gem] No bids on page {page_num}")
                        break

                    tenders = [self._parse_bid(b, page_num) for b in docs]
                    if org_filter:
                        tenders = [t for t in tenders
                                   if org_filter.lower() in t["organisation"].lower()]

                    result.tenders.extend(tenders)
                    result.pages = page_num
                    log.info(f"[gem] Page {page_num} — {len(tenders)} bids (total: {len(result.tenders)})")

                    if progress_cb:
                        await progress_cb(page_num, len(result.tenders))

                    total_found = (data.get("response") or {}).get("response", {}).get("numFound", 0)
                    current_start = (page_num - 1) * page_size
                    if current_start + page_size >= total_found:
                        break

                    # Click Next page button so the browser triggers next API call
                    next_btn = await page.query_selector(
                        "a.next, li.next a, a[aria-label='Next'], "
                        ".pagination a:has-text('Next'), .pagination li:last-child a"
                    )
                    if not next_btn:
                        # Try JavaScript-based pagination trigger
                        clicked = await page.evaluate(f"""() => {{
                            const links = Array.from(document.querySelectorAll('a, button'));
                            const btn = links.find(el =>
                                el.innerText.trim().toLowerCase() === 'next' ||
                                el.getAttribute('aria-label') === 'Next'
                            );
                            if (btn) {{ btn.click(); return true; }}
                            return false;
                        }}""")
                        if not clicked:
                            log.warning(f"[gem] No next button found at page {page_num}")
                            break
                    else:
                        await next_btn.click()

                    page_num += 1
                    await page.wait_for_timeout(3000)

            except Exception as e:
                log.error(f"[gem] Error: {e}")
                result.errors.append(str(e))
            finally:
                await ctx.close()

        finally:
            if own_session:
                await self.session.close()

        return result

    def _parse_bid(self, b: dict, page_num: int) -> dict:
        def arr(key):
            v = b.get(key, [])
            return v[0] if isinstance(v, list) and v else v or ""

        bid_no   = arr("b_bid_number")
        ministry = arr("ba_official_details_minName")
        dept     = arr("ba_official_details_deptName")
        org      = f"{ministry} — {dept}".strip(" —") if dept else ministry

        start_dt = arr("final_start_date_sort") or arr("b_start_date_sort")
        end_dt   = arr("final_end_date_sort")   or arr("b_end_date_sort")

        status_code = arr("b_status")
        status = "Active" if status_code == 1 else ("Closed" if status_code == 2 else str(status_code))

        return {
            "portal_id":            "gem",
            "portal_name":          "Government e-Marketplace (GeM)",
            "tender_id":            str(bid_no),
            "ref_number":           str(bid_no),
            "title":                arr("b_category_name") or arr("bd_category_name"),
            "organisation":         org,
            "published_date":       str(start_dt)[:19].replace("T", " "),
            "closing_date":         str(end_dt)[:19].replace("T", " "),
            "opening_date":         "",
            "status":               status,
            "detail_url":           f"https://bidplus.gem.gov.in/bidlists/biddetail/{bid_no}",
            "scraped_at":           now_iso(),
            "page_num":             page_num,
            "detail_scraped":       False,
            "tender_value_inr":     "",
            "tender_fee_inr":       "",
            "emd_inr":              "",
            "emd_fee_type":         "",
            "tender_type":          "GeM Bid" if arr("b_bid_type") != 2 else "GeM RA",
            "tender_category":      arr("b_category_name"),
            "product_category":     arr("bd_category_name"),
            "form_of_contract":     "",
            "payment_mode":         "",
            "bid_submission_start": str(start_dt)[:19].replace("T", " "),
            "bid_submission_end":   str(end_dt)[:19].replace("T", " "),
            "doc_download_start":   "",
            "doc_download_end":     "",
            "clarification_start":  "",
            "clarification_end":    "",
            "pre_bid_meeting":      "",
            "bid_validity":         "",
            "work_description":     "",
            "two_stage_bid":        "",
            "nda_allowed":          "",
            "location":             "",
            "pincode":              "",
            "contact":              "",
            "fee_payable_to":       "",
            "emd_payable_to":       "",
            "documents":            "",
            "gem_category":         arr("b_category_name"),
            "gem_quantity":         str(arr("b_total_quantity")),
            "gem_consignee":        "",
        }
