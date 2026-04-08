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
    // Extract ALL key-value pairs from every table on the page
    document.querySelectorAll("table tr").forEach(row => {
        const cells = Array.from(row.querySelectorAll("td, th"));
        if (cells.length >= 2) {
            const k = cells[0].innerText.trim().replace(/:$/, "").trim();
            const v = cells[1].innerText.trim();
            if (k && v && k.length < 150 && !k.match(/^\\d+$/)) {
                kv[k] = v.slice(0, 500);
            }
        }
        // Also handle 4-column rows (label, value, label, value)
        if (cells.length >= 4) {
            const k2 = cells[2].innerText.trim().replace(/:$/, "").trim();
            const v2 = cells[3].innerText.trim();
            if (k2 && v2 && k2.length < 150 && !k2.match(/^\\d+$/)) {
                kv[k2] = v2.slice(0, 500);
            }
        }
    });

    // Extract documents / attachments
    const docs = [];
    document.querySelectorAll("a[href*='download'], a[href*='document'], a[href*='.pdf'], a[href*='.doc']").forEach(a => {
        const text = a.innerText.trim();
        const href = a.href;
        if (text && href) docs.push(text + ' → ' + href);
    });

    // AOC / Award sections
    const aocSections = Array.from(document.querySelectorAll("h2, h3, h4, .aoc, .award, [class*='award'], [class*='result']"))
        .map(h => h.innerText.trim()).filter(t => t);

    // Full page text for any missed structured data
    const pageText = document.body ? document.body.innerText.slice(0, 5000) : "";

    return {kv, aocSections, docs, pageText};
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

    async def _fetch_details(self, tenders: list[dict], ctx, concurrency: int = 4) -> list[dict]:
        """Fetch detail pages with parallel workers for speed."""
        enriched = [None] * len(tenders)
        q = asyncio.Queue()
        for i, t in enumerate(tenders):
            await q.put((i, t))

        async def worker(worker_id: int):
            page = await self.session.new_page(ctx)
            try:
                while not q.empty():
                    try:
                        idx, t = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    url = t.get("detail_url", "")
                    if not url:
                        enriched[idx] = t
                        continue

                    try:
                        log.info(f"[cppp] W{worker_id} Detail {idx+1}/{len(tenders)}: {t.get('tender_id','?')}")
                        await page.goto(url, wait_until="networkidle", timeout=45_000)
                        await page.wait_for_timeout(800)
                        data = await page.evaluate(DETAIL_JS)
                        enriched[idx] = self._merge_detail(t, data)
                    except Exception as e:
                        log.warning(f"[cppp] W{worker_id} Detail failed {t.get('tender_id','?')}: {e}")
                        enriched[idx] = {**t, "detail_scraped": "False"}
                    await asyncio.sleep(1.0)
            finally:
                await page.close()

        workers = [asyncio.create_task(worker(i)) for i in range(min(concurrency, len(tenders)))]
        await asyncio.gather(*workers)

        return [e for e in enriched if e is not None]

    def _merge_detail(self, tender: dict, data: dict) -> dict:
        kv = data.get("kv", {})

        def get(*keys):
            """Fuzzy-match: try exact keys first, then case-insensitive substring."""
            for k in keys:
                v = kv.get(k, "")
                if v:
                    return v.replace("\n", " ").strip()
            # Fallback: case-insensitive partial match
            for k in keys:
                kl = k.lower()
                for actual_key, v in kv.items():
                    if kl in actual_key.lower() and v:
                        return v.replace("\n", " ").strip()
            return ""

        docs = data.get("docs", [])
        doc_str = " | ".join(docs[:20]) if docs else ""

        merged = {
            **tender,
            "detail_scraped":         "True",
            "tender_value_inr":       get("Tender Value in ₹", "Tender Value", "ECV", "Estimated Cost", "Estimated Contract Value"),
            "tender_fee_inr":         get("Tender Fee in ₹", "Tender Fee", "Cost of Tender"),
            "emd_inr":                get("EMD Amount in ₹", "EMD Amount", "EMD", "Earnest Money"),
            "emd_fee_type":           get("EMD Fee Type", "EMD Type"),
            "tender_type":            get("Tender Type", "Type of Tender"),
            "tender_category":        get("Tender Category", "Category"),
            "product_category":       get("Product Category", "Item Category"),
            "form_of_contract":       get("Form Of Contract", "Contract Form", "Contract Type"),
            "payment_mode":           get("Payment Mode", "Mode of Payment"),
            "bid_submission_start":   get("Bid Submission Start Date", "Submission Start"),
            "bid_submission_end":     get("Bid Submission End Date", "Submission End", "Last Date"),
            "doc_download_start":     get("Document Download / Sale Start Date", "Document Download Start", "Sale Start"),
            "doc_download_end":       get("Document Download / Sale End Date", "Document Download End", "Sale End"),
            "clarification_start":    get("Clarification Start Date", "Seeking Clarification Start"),
            "clarification_end":      get("Clarification End Date", "Seeking Clarification End"),
            "pre_bid_meeting":        get("Pre Bid Meeting Date", "Pre-Bid Meeting", "Pre Bid"),
            "bid_validity":           get("Bid Validity", "Validity", "Bid Validity Days"),
            "work_description":       get("Work Description", "Brief Description", "Description", "Scope of Work"),
            "two_stage_bid":          get("Two Stage Bid", "Allow Two Stage"),
            "nda_allowed":            get("Allow NDA", "NDA"),
            "location":               get("Location", "District", "Place of Work", "Delivery Location"),
            "pincode":                get("Pincode", "Pin Code"),
            "contact":                get("Contact", "Officer", "Contact Person", "Tender Inviting Authority"),
            "fee_payable_to":         get("Fee Payable To", "Tender Fee Payable To", "DD Payable To"),
            "emd_payable_to":         get("EMD Payable To", "EMD Payable At"),
            "documents":              doc_str or get("Documents", "Tender Documents"),
            # Award fields
            "award_winner":           get("Award To", "Successful Bidder", "Vendor Name", "L1 Bidder", "Awarded To"),
            "award_date":             get("Award of Contract Date", "AOC Date", "Award Date"),
            "award_amount":           get("Award Amount", "Contract Value", "Award Value", "Awarded Amount"),
            "aoc_no":                 get("AOC No.", "Award Reference", "Contract No.", "AOC Number"),
            # Extra fields from detail
            "department":             get("Department", "Dept", "Ministry"),
        }

        return merged

    def _parse_row(self, row: dict, page_num: int) -> dict:
        title     = row.get("title", "")
        ref_num   = row.get("ref_no", "")
        tender_id = row.get("tender_id", "") or ref_num or title[:80]

        return {
            "portal_id":            "cppp",
            "portal_name":          "Central Public Procurement Portal (CPPP)",
            "tender_id":            tender_id or title[:80],
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
