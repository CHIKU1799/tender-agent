"""
GeM Agent — Government e-Marketplace
Uses the public REST API — no browser/CAPTCHA needed.
API: GET https://bidplus.gem.gov.in/rest/bidlists?searchedCriteria=&pageNo=1
"""
from __future__ import annotations
import asyncio
import logging
import aiohttp
from core.utils import now_iso
from agents.base import BaseAgent, ScrapeResult
from portals.configs import PortalConfig

log = logging.getLogger("gem")

GEM_API = "https://bidplus.gem.gov.in/rest/bidlists"
GEM_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://bidplus.gem.gov.in/all-bids",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}


class GeMAgent(BaseAgent):
    """Scrapes GeM via its public REST API."""

    def __init__(self, config: PortalConfig):
        super().__init__(config)

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id="gem")

        async with aiohttp.ClientSession(headers=GEM_HEADERS) as session:
            page_num = 1
            while True:
                if max_pages and page_num > max_pages:
                    break

                params = {"searchedCriteria": "", "pageNo": page_num}
                try:
                    async with session.get(GEM_API, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            log.warning(f"[gem] Page {page_num}: HTTP {resp.status}")
                            break
                        data = await resp.json(content_type=None)
                except Exception as e:
                    log.error(f"[gem] Page {page_num} error: {e}")
                    result.errors.append(str(e))
                    break

                bid_list = (
                    data.get("data", {}).get("bidlist", [])
                    if isinstance(data, dict) else []
                )
                if not bid_list:
                    log.info(f"[gem] No more bids at page {page_num}")
                    break

                tenders = [self._parse_bid(b) for b in bid_list]

                if org_filter:
                    tenders = [
                        t for t in tenders
                        if org_filter.lower() in t["organisation"].lower()
                    ]

                result.tenders.extend(tenders)
                result.pages = page_num

                log.info(
                    f"[gem] Page {page_num} — {len(tenders)} bids "
                    f"(total: {len(result.tenders)})"
                )

                if progress_cb:
                    await progress_cb(page_num, len(result.tenders))

                page_num += 1
                await asyncio.sleep(1.5)  # polite delay for API

        return result

    def _parse_bid(self, bid: dict) -> dict:
        bid_no = bid.get("bid_number", bid.get("bidNumber", ""))
        return {
            "portal_id":      "gem",
            "tender_id":      bid_no,
            "ref_number":     bid_no,
            "title":          bid.get("bid_title_dtls", bid.get("bidTitleDtls", "")),
            "organisation":   bid.get("ministry_name", bid.get("ministryName", "")),
            "published_date": bid.get("bid_start_dt", bid.get("bidStartDt", "")),
            "closing_date":   bid.get("bid_end_dt", bid.get("bidEndDt", "")),
            "opening_date":   "",
            "status":         bid.get("bid_status", bid.get("bidStatus", "Active")),
            "detail_url":     f"https://bidplus.gem.gov.in/bidlists/biddetail/{bid_no}",
            "scraped_at":     now_iso(),
            "page_num":       0,
            # Extra GeM-specific fields
            "gem_category":   bid.get("category", ""),
            "gem_quantity":   bid.get("quantity", ""),
            "gem_consignee":  bid.get("consignee", ""),
        }
