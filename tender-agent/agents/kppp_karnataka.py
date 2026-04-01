"""
Karnataka KPPP Agent — kppp.karnataka.gov.in
Uses the portal's clean REST API — no Playwright, no CAPTCHA.

API endpoint:
  POST https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/portal-service/search-eproc-tenders
  ?page=<N>&size=20&order-by-tender-publish=true

Request body:
  {"tenderNumber": "", "title": "", "category": "GOODS|WORKS|SERVICES",
   "status": "PUBLISHED", "location": null,
   "publishedFromDate": null, "publishedToDate": null,
   "tenderClosureFromDate": null, "tenderClosureToDate": null}

Response: flat JSON list (empty list → no more pages).
~6 000+ live tenders across 3 categories.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Any

from agents.base import BaseAgent, ScrapeResult
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("kppp_karnataka")

API_URL    = (
    "https://kppp.karnataka.gov.in/supplier-registration-service"
    "/v1/api/portal-service/search-eproc-tenders"
)
PAGE_SIZE  = 20
CATEGORIES = ["GOODS", "WORKS", "SERVICES"]

HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "User-Agent":   "Mozilla/5.0 (compatible; TenderAgent/1.0)",
}


class KPPPKarnatakaAgent(BaseAgent):
    """Scrapes Karnataka KPPP via REST API — no browser session required."""

    def __init__(self, config: PortalConfig, session=None):
        super().__init__(config)
        # session is accepted for interface compatibility but not used

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.portal_id)
        global_page = 0  # cross-category page counter for progress_cb
        loop = asyncio.get_event_loop()

        for category in CATEGORIES:
            api_page = 0  # zero-indexed page for this category

            while True:
                if max_pages and api_page >= max_pages:
                    break

                tenders, err = await loop.run_in_executor(
                    None, self._fetch_page_sync, category, api_page
                )

                if err:
                    log.error(f"[kppp] {category} p{api_page}: {err}")
                    result.errors.append(f"{category} p{api_page}: {err}")
                    break

                if not tenders:
                    log.info(f"[kppp] {category} — no more results after page {api_page}")
                    break

                parsed = [self._parse(t) for t in tenders]

                if org_filter:
                    parsed = [t for t in parsed
                              if org_filter.lower() in t["organisation"].lower()]

                result.tenders.extend(parsed)
                global_page += 1
                result.pages = global_page

                log.info(
                    f"[kppp] {category} p{api_page} — "
                    f"{len(parsed)} tenders (running total: {len(result.tenders)})"
                )

                if progress_cb:
                    await progress_cb(global_page, len(result.tenders))

                if len(tenders) < PAGE_SIZE:
                    # Last partial page — no more data for this category
                    break

                api_page += 1
                await asyncio.sleep(0.5)   # be polite

        return result

    def _fetch_page_sync(self, category: str, page: int) -> tuple[list[dict], str]:
        """Blocking HTTP POST — called via run_in_executor so it doesn't block the loop."""
        url = (
            f"{API_URL}"
            f"?page={page}&size={PAGE_SIZE}&order-by-tender-publish=true"
        )
        body: dict[str, Any] = {
            "tenderNumber":          "",
            "title":                 "",
            "category":              category,
            "status":                "PUBLISHED",
            "location":              None,
            "publishedFromDate":     None,
            "publishedToDate":       None,
            "tenderClosureFromDate": None,
            "tenderClosureToDate":   None,
        }
        payload = json.dumps(body).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        for k, v in HEADERS.items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                if isinstance(data, list):
                    return data, ""
                if isinstance(data, dict):
                    for key in ("content", "data", "tenders", "result"):
                        if isinstance(data.get(key), list):
                            return data[key], ""
                return [], ""
        except urllib.error.HTTPError as e:
            return [], f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return [], str(e)

    def _parse(self, t: dict) -> dict:
        tender_id = str(t.get("tenderNumber") or t.get("id") or "")
        title     = (t.get("title") or t.get("description") or "").strip()
        org       = (t.get("deptName") or "").strip()
        location  = (t.get("locationName") or "").strip()
        pub_date  = _fmt_date(t.get("publishedDate"))
        close_date = _fmt_date(t.get("tenderClosureDate"))
        ecv       = str(t.get("ecv") or "")
        category  = (t.get("categoryText") or t.get("category") or "").strip()
        strategy  = (t.get("invitingStrategyText") or "").strip()
        nit_id    = str(t.get("nitId") or "")
        status    = (t.get("statusText") or "Active").strip()
        detail_url = (
            f"https://kppp.karnataka.gov.in/tenders/{tender_id}"
            if tender_id else ""
        )

        return {
            "portal_id":            self.portal_id,
            "portal_name":          self.config.display_name,
            "tender_id":            tender_id,
            "ref_number":           nit_id,
            "title":                title,
            "organisation":         org,
            "published_date":       pub_date,
            "closing_date":         close_date,
            "opening_date":         "",
            "status":               status,
            "detail_url":           detail_url,
            "scraped_at":           now_iso(),
            "page_num":             0,
            "detail_scraped":       False,
            "tender_value_inr":     ecv,
            "tender_fee_inr":       "",
            "emd_inr":              "",
            "emd_fee_type":         "",
            "tender_type":          strategy,
            "tender_category":      category,
            "product_category":     "",
            "form_of_contract":     "",
            "payment_mode":         "",
            "bid_submission_start": "",
            "bid_submission_end":   close_date,
            "doc_download_start":   "",
            "doc_download_end":     "",
            "clarification_start":  "",
            "clarification_end":    "",
            "pre_bid_meeting":      "",
            "bid_validity":         "",
            "work_description":     "",
            "two_stage_bid":        "",
            "nda_allowed":          "",
            "location":             location,
            "pincode":              "",
            "contact":              "",
            "fee_payable_to":       "",
            "emd_payable_to":       "",
            "documents":            "",
            "award_winner":         "",
            "award_date":           "",
            "award_amount":         "",
            "aoc_no":               "",
            "gem_category":         "",
            "gem_quantity":         "",
            "gem_consignee":        "",
        }


def _fmt_date(raw) -> str:
    """Normalise various date representations to an ISO-ish string."""
    if not raw:
        return ""
    if isinstance(raw, (int, float)):
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(raw)
    s = str(raw).strip()
    # Already looks like an ISO date — keep it
    return s


# ── Standalone smoke-test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    async def _main():
        # Minimal config object
        cfg = PortalConfig(
            portal_id    = "kppp_karnataka",
            display_name = "Karnataka KPPP",
            base_url     = "https://kppp.karnataka.gov.in",
            platform     = "kppp",
            category     = "State",
        )
        agent = KPPPKarnatakaAgent(cfg)

        print("\n── Karnataka KPPP smoke-test (2 pages × 3 categories = up to 120 tenders) ──\n")

        result = await agent.scrape(max_pages=2)

        if result.errors:
            print("Errors encountered:")
            for e in result.errors:
                print(f"  {e}")

        total = len(result.tenders)
        print(f"\nTotal scraped: {total} tenders across {result.pages} API pages\n")

        # Print first 40
        for i, t in enumerate(result.tenders[:40], 1):
            print(
                f"{i:3}. [{t['tender_category']:<8}] "
                f"{t['tender_id']:<20} "
                f"{t['title'][:60]:<60}  "
                f"{t['closing_date']}"
            )

        if total == 0:
            print("\nNO tenders returned — check the API or network.")
            sys.exit(1)

    asyncio.run(_main())
