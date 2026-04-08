"""
Karnataka KPPP Tender Agent
────────────────────────────────────────────────────────────────────────────
Portal  : https://kppp.karnataka.gov.in/#/portal/searchTender/live
Platform: Angular Material SPA — client-side canvas CAPTCHA

Strategy (Direct API)
---------------------
1. Load portal page; intercept canvas fillText to auto-solve the CAPTCHA.
2. After captcha, use the Angular app's REST API directly via page.evaluate(fetch).
3. POST  /search-eproc-tenders?page=N&size=20  → listing (array of tender summaries)
4. GET   /{nitId}/{category}-tender-full-view   → full tender detail
5. Paginate through all pages; fetch detail for every tender.
6. Call save_callback after each page for incremental persistence.

Usage
-----
    from agents.kppp import KPPPAgent
    agent = KPPPAgent(cfg, session)
    result = await agent.scrape(max_pages=50, fetch_details=True)
    # Scrape closed + awarded tenders:
    result = await agent.scrape(scope="all", max_pages=50)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("kppp")
SCREENSHOTS_DIR = Path("screenshots")
PORTAL_URL = "https://kppp.karnataka.gov.in/#/portal/searchTender/live"

# ── fillText hook script (injected BEFORE page loads) ────────────────────────
CAPTCHA_HOOK_JS = """
const _origFillText = CanvasRenderingContext2D.prototype.fillText;
window.__captchaTexts = [];
CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxWidth) {
    window.__captchaTexts.push({text, canvas_id: this.canvas?.id || ''});
    return _origFillText.apply(this, arguments);
};
"""

# ── API JavaScript helpers (run inside page context) ─────────────────────────

JS_FETCH_PAGE = """async (args) => {
    const body = {
        tenderNumber: '',
        category: args.category,
        status: args.status,
        publishedFromDate: null,
        publishedToDate: null,
        title: '',
        location: null,
        tenderClosureFromDate: null,
        tenderClosureToDate: null
    };
    // WORKS and SERVICES use different API endpoints than GOODS
    const pathPrefix = '/supplier-registration-service/v1/api/portal-service/';
    let endpoint;
    if (args.category === 'WORKS') {
        endpoint = pathPrefix + 'works/search-eproc-tenders';
    } else if (args.category === 'SERVICES') {
        endpoint = pathPrefix + 'services/search-eproc-tenders';
    } else {
        endpoint = pathPrefix + 'search-eproc-tenders';
    }
    const resp = await fetch(
        endpoint + '?page=' + args.page + '&size=20&order-by-tender-publish=true',
        {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            body: JSON.stringify(body)
        }
    );
    if (resp.status !== 200) return {error: resp.status, tenders: []};
    const data = await resp.json();
    return {error: null, tenders: Array.isArray(data) ? data : []};
}"""

JS_FETCH_DETAIL = """async (args) => {
    try {
        const resp = await fetch(
            '/supplier-registration-service/v1/api/portal-service/'
            + args.nitId + '/' + args.category + '-tender-full-view',
            { headers: {'Accept': 'application/json'} }
        );
        if (resp.status !== 200) return {error: resp.status};
        return await resp.json();
    } catch(e) {
        return {error: e.message};
    }
}"""

# ── Scope → API status mapping ──────────────────────────────────────────────
#   scope       → API statuses included
#   "active"    → PUBLISHED only (default, backward-compatible)
#   "archive"   → CLOSED only
#   "awards"    → AWARDED only
#   "both"      → CLOSED + AWARDED
#   "all"       → PUBLISHED + CLOSED + AWARDED
SCOPE_STATUS_MAP: dict[str, list[str]] = {
    "active":  ["PUBLISHED"],
    "archive": ["CLOSED"],
    "awards":  ["AWARDED"],
    "both":    ["CLOSED", "AWARDED"],
    "all":     ["PUBLISHED", "CLOSED", "AWARDED"],
}

# Human-friendly label for each API status
STATUS_LABEL: dict[str, str] = {
    "PUBLISHED": "Active",
    "CLOSED":    "Archive",
    "AWARDED":   "Awarded",
}

# Categories
CATEGORIES = ["GOODS", "WORKS", "SERVICES"]

# NIT DTO field mapping → our schema
NIT_FIELD_MAP = {
    "tenderFee":              "tender_fee_inr",
    "emd":                    "emd_inr",
    "evaluationTypeText":     "tender_type",
    "invitingStrategyText":   "form_of_contract",
    "tenderReceiptClose":     "closing_date",
    "tenderQueryClose":       "clarification_end",
    "technicalBidOpen":       "opening_date",
    "publishedDate":          "published_date",
    "bidValidityPeriod":      "bid_validity",
    "preBidMeetingDate":      "pre_bid_meeting",
    "contactPerson":          "contact",
    "mobileNumber":           "pincode",      # store phone in pincode for now
    "bidSubmissionStartDate": "bid_submission_start",
    "denominationTypeText":   "payment_mode",
    "noOfCalls":              "two_stage_bid",
}


class KPPPAgent(BaseAgent):
    """Scrapes Karnataka KPPP via direct API calls — auto-solves client-side captcha."""

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = True,
        progress_cb=None,
        tabs: list[str] | None = None,
        save_callback=None,
        scope: str = "active",
    ) -> ScrapeResult:
        result = ScrapeResult(portal_id=self.portal_id)
        ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)
        page.set_default_timeout(30_000)

        # Resolve which API statuses to scrape based on scope
        api_statuses = SCOPE_STATUS_MAP.get(scope)
        if api_statuses is None:
            raise ValueError(
                f"Invalid scope '{scope}'. "
                f"Must be one of: {', '.join(SCOPE_STATUS_MAP)}"
            )

        try:
            # Step 1: Load page and solve captcha
            await self._load_and_solve_captcha(page)

            # Step 2: Scrape all statuses × categories via API
            categories = [c.upper() for c in (tabs or ["goods", "works", "services"])]
            page_total = 0

            for api_status in api_statuses:
                status_label = STATUS_LABEL.get(api_status, api_status)
                log.info(f"[kppp] ═══ Status: {api_status} (label: {status_label}) ═══")

                for category in categories:
                    log.info(f"[kppp] ── {api_status} / {category} ─────────────────────")
                    pg = 0

                    while True:
                        if max_pages and pg >= max_pages:
                            break

                        api_result = await page.evaluate(JS_FETCH_PAGE, {
                            "category": category,
                            "status": api_status,
                            "page": pg,
                        })

                        if api_result.get("error"):
                            log.warning(f"[kppp]   API error on page {pg}: {api_result['error']}")
                            break

                        raw_tenders = api_result.get("tenders", [])
                        if not raw_tenders:
                            log.info(f"[kppp]   Page {pg}: empty — done with {api_status}/{category}")
                            break

                        # Map to our schema
                        tenders_on_page = []
                        for raw in raw_tenders:
                            tender = self._map_listing(raw, category)
                            # Tag with the correct status label
                            tender["status"] = status_label

                            # Fetch detail for each tender
                            if fetch_details and raw.get("nitId") and raw.get("category"):
                                detail = await self._fetch_detail(
                                    page, raw["nitId"], raw["category"].lower()
                                )
                                if detail:
                                    for k, v in detail.items():
                                        if v and not tender.get(k):
                                            tender[k] = v
                                    tender["detail_scraped"] = "True"

                            tenders_on_page.append(tender)

                        # Apply org filter
                        if org_filter:
                            tenders_on_page = [
                                t for t in tenders_on_page
                                if org_filter.lower() in t.get("organisation", "").lower()
                            ]

                        result.tenders.extend(tenders_on_page)
                        page_total += 1
                        result.pages = page_total
                        pg += 1

                        log.info(
                            f"[kppp]   Page {pg} — {len(tenders_on_page)} tenders "
                            f"(cumulative: {len(result.tenders)})"
                        )

                        # Incremental save
                        if save_callback:
                            try:
                                save_callback(self.portal_id, result.tenders)
                            except Exception as e:
                                log.warning(f"[kppp]   Save error: {e}")

                        if progress_cb:
                            await progress_cb(page_total, len(result.tenders))

                        # Small delay between pages to be polite
                        await asyncio.sleep(1)

        except Exception as e:
            log.error(f"[kppp] Fatal: {e}", exc_info=True)
            result.errors.append(str(e))
            try:
                await page.screenshot(path=str(SCREENSHOTS_DIR / "kppp_fatal_error.png"))
            except Exception:
                pass
        finally:
            await ctx.close()

        return result

    # ── Captcha ──────────────────────────────────────────────────────────────

    async def _load_and_solve_captcha(self, page, max_retries: int = 5):
        """Load portal, hook canvas fillText, solve captcha automatically."""

        # Inject fillText hook BEFORE navigation
        await page.context.add_init_script(CAPTCHA_HOOK_JS)

        log.info(f"[kppp] Loading {PORTAL_URL}")
        await page.goto(PORTAL_URL, wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(4)

        for attempt in range(1, max_retries + 1):
            # Read the captured captcha text
            captured = await page.evaluate("() => window.__captchaTexts || []")
            captcha_text = ""
            for entry in reversed(captured):  # last one is most recent
                if entry.get("canvas_id") == "captcahCanvas":
                    captcha_text = entry["text"]
                    break

            if not captcha_text:
                log.warning(f"[kppp] Attempt {attempt}: no captcha text found, retrying...")
                # Click reload captcha button
                reload_btn = page.locator("a.reload, a.cpt-btn.reload")
                if await reload_btn.count() > 0:
                    await reload_btn.first.click()
                    await asyncio.sleep(2)
                continue

            log.info(f"[kppp] Captcha text (attempt {attempt}): {captcha_text}")

            # Enter captcha
            captcha_input = page.locator('.captcha-actions input[type="text"]')
            await captcha_input.fill(captcha_text)
            await asyncio.sleep(0.3)

            # Click Check
            check_btn = page.locator('.captcha-actions input[type="button"][value="Check"]')
            await check_btn.click()
            await asyncio.sleep(4)

            # Verify: check if the search form / results appeared
            # After successful captcha, the "Live Tenders" search form appears
            body_text = await page.evaluate("() => document.body.innerText.slice(0, 2000)")
            if "Live Tenders" in body_text or "ಟೆಂಡರ್‌ಗಳನ್ನು ಹುಡುಕಿ" in body_text:
                log.info("[kppp] Captcha solved — search form loaded")
                await page.screenshot(path=str(SCREENSHOTS_DIR / "kppp_captcha_solved.png"))
                return

            log.warning(f"[kppp] Captcha attempt {attempt} may have failed, retrying...")
            # Clear and reload captcha
            reload_btn = page.locator("a.reload, a.cpt-btn.reload")
            if await reload_btn.count() > 0:
                await reload_btn.first.click()
                await asyncio.sleep(2)

        raise RuntimeError("Failed to solve KPPP captcha after max retries")

    # ── Detail fetch ─────────────────────────────────────────────────────────

    async def _fetch_detail(self, page, nit_id: int, category: str) -> dict | None:
        """Fetch full tender detail via API."""
        try:
            raw = await page.evaluate(JS_FETCH_DETAIL, {
                "nitId": nit_id,
                "category": category,
            })

            if raw.get("error"):
                log.warning(f"[kppp]     Detail error nit={nit_id}: {raw['error']}")
                return None

            return self._map_detail(raw)

        except Exception as e:
            log.warning(f"[kppp]     Detail exception nit={nit_id}: {e}")
            return None

    # ── Field mapping ────────────────────────────────────────────────────────

    def _map_listing(self, raw: dict, category: str) -> dict:
        """Map API listing record to our schema."""
        return {
            "portal_id":       self.portal_id,
            "portal_name":     "Karnataka Public Procurement Portal (KPPP)",
            "tender_id":       str(raw.get("id", "")),
            "ref_number":      raw.get("tenderNumber", ""),
            "title":           raw.get("title", ""),
            "work_description": raw.get("description", ""),
            "organisation":    raw.get("deptName", ""),
            "location":        raw.get("locationName", ""),
            "published_date":  raw.get("publishedDate", ""),
            "closing_date":    raw.get("tenderClosureDate", ""),
            "status":          raw.get("statusText", raw.get("status", "Active")),
            "tender_type":     raw.get("invitingStrategyText", ""),
            "tender_category": raw.get("categoryText", category),
            "tender_value_inr": str(raw.get("ecv", "")) if raw.get("ecv") else "",
            "scraped_at":      now_iso(),
            "kppp_tab":        category.lower(),
            "detail_scraped":  "False",
            "detail_url":      f"https://kppp.karnataka.gov.in/#/portal/viewTenderDetails",
        }

    def _map_detail(self, raw: dict) -> dict:
        """Map full detail API response to our schema fields."""
        detail: dict = {}

        # noticeInvitingTenderDTO has the richest data
        nit = raw.get("noticeInvitingTenderDTO") or {}
        for api_key, our_key in NIT_FIELD_MAP.items():
            val = nit.get(api_key)
            if val is not None:
                detail[our_key] = str(val).strip()

        # Phone number → contact field (not pincode)
        phone = nit.get("mobileNumber", "")
        contact_person = nit.get("contactPerson", "")
        if contact_person or phone:
            detail["contact"] = f"{contact_person} ({phone})" if phone else contact_person
            detail.pop("pincode", None)  # remove the incorrect mapping

        # Address
        addr = raw.get("tenderAddress") or {}
        if isinstance(addr, dict):
            parts = [str(v) for v in addr.values() if v and str(v).strip()]
            if parts:
                detail["contact"] = detail.get("contact", "") + " | " + ", ".join(parts)
            if addr.get("pinCode"):
                detail["pincode"] = str(addr["pinCode"])

        # EMD details
        if nit.get("emd"):
            detail["emd_inr"] = str(nit["emd"])
        if nit.get("emdBankGuarantee"):
            detail["emd_fee_type"] = "Bank Guarantee"
        elif nit.get("emdCash"):
            detail["emd_fee_type"] = "Cash"

        # Tender fee
        if nit.get("tenderFee"):
            detail["tender_fee_inr"] = str(nit["tenderFee"])

        # Bid validity
        if nit.get("bidValidityPeriod"):
            detail["bid_validity"] = f"{nit['bidValidityPeriod']} days"

        # Items list → work_description
        items = raw.get("tenderGoodsItemsList") or raw.get("tenderWorksItemsList") or []
        if items and isinstance(items, list):
            items_text = []
            for item in items[:20]:  # cap at 20 items
                if isinstance(item, dict):
                    name = item.get("itemName", item.get("description", ""))
                    qty = item.get("quantity", "")
                    uom = item.get("uom", item.get("uomText", ""))
                    items_text.append(f"- {name} (Qty: {qty} {uom})" if qty else f"- {name}")
            if items_text:
                detail["work_description"] = "\n".join(items_text)[:4000]

        # Eligibility criteria
        eligibility = raw.get("tenderEligibilityCriterionList") or []
        if eligibility and isinstance(eligibility, list):
            elig_text = [str(e.get("description", e.get("criterion", ""))) for e in eligibility if isinstance(e, dict)]
            if elig_text:
                existing = detail.get("work_description", "")
                detail["work_description"] = (existing + "\n\n[Eligibility]: " + "; ".join(elig_text))[:4000]

        # Schedule/delivery
        schedule = raw.get("tenderSchedule") or {}
        if isinstance(schedule, dict):
            if schedule.get("bidSubmissionStartDate"):
                detail["bid_submission_start"] = str(schedule["bidSubmissionStartDate"])
            if schedule.get("bidSubmissionEndDate"):
                detail["closing_date"] = str(schedule["bidSubmissionEndDate"])
            if schedule.get("docDownloadStartDate"):
                detail["doc_download_start"] = str(schedule["docDownloadStartDate"])
            if schedule.get("docDownloadEndDate"):
                detail["doc_download_end"] = str(schedule["docDownloadEndDate"])

        # Award info
        award = raw.get("tenderAwardDatesDTO") or {}
        if isinstance(award, dict):
            if award.get("awardDate"):
                detail["award_date"] = str(award["awardDate"])
            if award.get("awardAmount"):
                detail["award_amount"] = str(award["awardAmount"])

        # Store raw JSON of key sections for maximum data capture
        raw_sections = {}
        for key in ["tenderGroups", "tenderCriterionDocumentList",
                     "deliveryScheduleList", "tenderTechnicalParameterList",
                     "tenderSampleDTO", "evalStagesCompletedInfoDTO"]:
            val = raw.get(key)
            if val:
                raw_sections[key] = val
        if raw_sections:
            existing = detail.get("documents", "")
            raw_json = json.dumps(raw_sections, ensure_ascii=False, default=str)
            detail["documents"] = (existing + raw_json)[:4000] if existing else raw_json[:4000]

        return detail
