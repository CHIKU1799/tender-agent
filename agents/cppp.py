"""
CPPP Agent — Central Public Procurement Portal (eprocure.gov.in/cppp)
Aggregates tenders from ALL Indian government portals.

Accessible pages (NO CAPTCHA):
  Active tenders  → latestactivetendersnew/cpppdata?page=N
  Active corrigendums → activecorrigendumnew/cpppdata?page=N

NOTE: Award results (ResultOfTenders) require CAPTCHA on CPPP.
      Past/closed tenders require CAPTCHA for the archive search.
      Use `max_pages` to paginate deeper for older active tenders.
"""
from __future__ import annotations
import asyncio
import logging
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from agents.base import BaseAgent, ScrapeResult
from portals.configs import PortalConfig

log = logging.getLogger("cppp")

BASE_URL = "https://eprocure.gov.in/cppp"

EXTRACT_JS = """() => {
    const table = Array.from(document.querySelectorAll("table")).find(t =>
        t.querySelector("th") && t.querySelectorAll("tbody tr").length > 0
    );
    if (!table) return {rows: [], hasNext: false, totalText: ""};

    const rows = Array.from(table.querySelectorAll("tbody tr")).map(row => {
        const cells = Array.from(row.querySelectorAll("td"));
        const titleCell = cells[4];
        const linkEl = titleCell ? titleCell.querySelector("a") : null;

        // Title = link text; remaining text after title = /ref_no/tender_id
        const fullText  = titleCell ? titleCell.innerText.trim() : "";
        const linkText  = linkEl ? linkEl.innerText.trim() : "";
        // After removing the title, the rest is "/ref/tender_id"
        const remainder = fullText.startsWith(linkText)
            ? fullText.slice(linkText.length).replace(/^\\/+/, "")
            : fullText;
        const remParts  = remainder ? remainder.split("/") : [];
        const tender_id = remParts.length > 0 ? remParts[remParts.length - 1].trim() : "";
        const ref_no    = remParts.length > 1
            ? remParts.slice(0, remParts.length - 1).join("/").trim()
            : (remParts[0] || "").trim();

        return {
            published_date: cells[1] ? cells[1].innerText.trim() : "",
            closing_date:   cells[2] ? cells[2].innerText.trim() : "",
            opening_date:   cells[3] ? cells[3].innerText.trim() : "",
            title:          linkText || fullText,
            ref_no:         ref_no,
            tender_id:      tender_id,
            organisation:   cells[5] ? cells[5].innerText.trim() : "",
            detail_href:    linkEl ? linkEl.href : "",
        };
    }).filter(r => r.title || r.detail_href);

    // Pager
    const pager = document.querySelector(".pagination");
    const nextLink = pager ? Array.from(pager.querySelectorAll("a")).find(a =>
        a.innerText.trim() === ">" || a.innerText.trim() === "Next" ||
        a.getAttribute("aria-label") === "Next"
    ) : null;
    const totalText = (document.body.innerText.match(/Showing[^\\n]+entries/i) || [""])[0];

    return {rows, hasNext: !!nextLink, totalText};
}"""

DETAIL_JS = """() => {
    const kv = {};
    document.querySelectorAll("table tr").forEach(row => {
        const cells = Array.from(row.querySelectorAll("td"));
        if (cells.length >= 2) {
            const k = cells[0].innerText.trim().replace(/:$/, "").trim();
            const v = cells[1].innerText.trim();
            if (k && v && k.length < 100 && !k.match(/^\\d+$/)) {
                kv[k] = v.slice(0, 300);
            }
        }
    });
    const aocSections = Array.from(document.querySelectorAll("h2, h3, .aoc, .award")).map(h=>h.innerText.trim());
    return {kv, aocSections};
}"""


class CPPPAgent(BaseAgent):
    """Scrapes CPPP (Central Public Procurement Portal) — cross-ministry, no CAPTCHA."""

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.portal_id)
        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            current_page = 1

            while True:
                if max_pages and current_page > max_pages:
                    break

                url = f"{BASE_URL}/latestactivetendersnew/cpppdata?page={current_page}"
                log.info(f"[cppp] Loading page {current_page}: {url}")
                await page.goto(url, wait_until="networkidle", timeout=60_000)
                await page.wait_for_timeout(2000)

                data = await page.evaluate(EXTRACT_JS)
                rows = data.get("rows", [])

                if not rows:
                    log.info(f"[cppp] No rows on page {current_page} — done")
                    break

                tenders = [self._parse_row(r, current_page) for r in rows]
                if org_filter:
                    tenders = [t for t in tenders
                               if org_filter.lower() in t["organisation"].lower()]

                result.tenders.extend(tenders)
                result.pages = current_page

                total_text = data.get("totalText", "")
                log.info(f"[cppp] Page {current_page} — {len(tenders)} tenders (total: {len(result.tenders)}) {total_text}")

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                # Check next page: use URL pagination (no JS click needed)
                if not data.get("hasNext", False) and len(rows) < 10:
                    # < 10 rows means last page
                    break

                current_page += 1
                await random_delay(1.0, 2.5)

            # Optional: fetch detail pages
            if fetch_details and result.tenders:
                log.info(f"[cppp] Fetching {len(result.tenders)} detail pages...")
                result.tenders = await self._fetch_details(result.tenders, ctx)

        except Exception as e:
            log.error(f"[cppp] Error: {e}")
            result.errors.append(str(e))
        finally:
            await ctx.close()

        return result

    async def _fetch_details(self, tenders: list[dict], ctx) -> list[dict]:
        page = await self.session.new_page(ctx)
        enriched = []
        try:
            for i, t in enumerate(tenders, 1):
                url = t.get("detail_url", "")
                if not url:
                    enriched.append(t)
                    continue
                try:
                    log.info(f"[cppp] Detail {i}/{len(tenders)}: {t.get('tender_id','?')}")
                    await page.goto(url, wait_until="networkidle", timeout=60_000)
                    await page.wait_for_timeout(1000)
                    data = await page.evaluate(DETAIL_JS)
                    enriched.append(self._merge_detail(t, data))
                except Exception as e:
                    log.warning(f"[cppp] Detail failed: {e}")
                    enriched.append({**t, "detail_scraped": False})
                if i < len(tenders):
                    await asyncio.sleep(2.0)
        finally:
            await page.close()
        return enriched

    def _merge_detail(self, tender: dict, data: dict) -> dict:
        kv = data.get("kv", {})

        def get(*keys):
            for k in keys:
                v = kv.get(k, "")
                if v:
                    return v.replace("\n", " ").strip()
            return ""

        return {
            **tender,
            "detail_scraped":         True,
            "tender_value_inr":       get("Tender Value in ₹", "ECV", "Estimated Cost"),
            "tender_fee_inr":         get("Tender Fee in ₹", "Tender Fee"),
            "emd_inr":                get("EMD Amount in ₹", "EMD"),
            "emd_fee_type":           get("EMD Fee Type"),
            "tender_type":            get("Tender Type"),
            "tender_category":        get("Tender Category"),
            "product_category":       get("Product Category"),
            "form_of_contract":       get("Form Of Contract"),
            "payment_mode":           get("Payment Mode"),
            "bid_submission_start":   get("Bid Submission Start Date"),
            "bid_submission_end":     get("Bid Submission End Date"),
            "doc_download_start":     get("Document Download / Sale Start Date"),
            "doc_download_end":       get("Document Download / Sale End Date"),
            "work_description":       get("Work Description"),
            "location":               get("Location", "District"),
            "pincode":                get("Pincode"),
            "contact":                get("Contact"),
            # Award fields
            "award_winner":           get("Award To", "Successful Bidder", "Vendor Name", "L1 Bidder"),
            "award_date":             get("Award of Contract Date", "AOC Date"),
            "award_amount":           get("Award Amount", "Contract Value", "Award Value"),
            "aoc_no":                 get("AOC No.", "Award Reference", "Contract No."),
        }

    def _parse_row(self, row: dict, page_num: int) -> dict:
        title     = row.get("title", "")
        ref_num   = row.get("ref_no", "")
        tender_id = row.get("tender_id", "") or ref_num or title[:80]

        return {
            "portal_id":            "cppp",
            "portal_name":          "Central Public Procurement Portal (CPPP)",
            "tender_id":            tender_id or title_raw[:80],
            "ref_number":           ref_num,
            "title":                title,
            "organisation":         row.get("organisation", ""),
            "published_date":       row.get("published_date", ""),
            "closing_date":         row.get("closing_date", ""),
            "opening_date":         row.get("opening_date", ""),
            "status":               "Active",
            "detail_url":           row.get("detail_href", ""),
            "scraped_at":           now_iso(),
            "page_num":             page_num,
            "detail_scraped":       False,
            "tender_value_inr":     "",
            "tender_fee_inr":       "",
            "emd_inr":              "",
            "emd_fee_type":         "",
            "tender_type":          "",
            "tender_category":      "",
            "product_category":     "",
            "form_of_contract":     "",
            "payment_mode":         "",
            "bid_submission_start": "",
            "bid_submission_end":   "",
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
            "award_winner":         "",
            "award_date":           "",
            "award_amount":         "",
            "aoc_no":               "",
            "gem_category":         "",
            "gem_quantity":         "",
            "gem_consignee":        "",
        }
