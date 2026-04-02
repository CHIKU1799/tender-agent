"""
Karnataka e-Procurement Agent
Portal: https://eproc.karnataka.gov.in/eprocportal/pages/index.jsp
Platform: JSF/Seam (not GePNIC)

Approach:
  - Navigate to eproc_tenders_list.seam
  - Fill date range (last N days → today) + status = PUBLISHED
  - Submit form, parse results table
  - Paginate via "Next" if available
  - No CAPTCHA
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("karnataka")

TENDERS_URL = "https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam"

EXTRACT_JS = """() => {
    // Find the results table — it has columns: Ref No, Title, Department, Dates, Links
    const allTables = Array.from(document.querySelectorAll("table"));

    // Find table with most anchor links (that's the results table)
    let best = null, maxLinks = 0;
    for (const t of allTables) {
        const n = t.querySelectorAll("a[href*='tender'], a[href*='Tender'], a[href*='view']").length;
        if (n > maxLinks) { maxLinks = n; best = t; }
    }
    if (!best || maxLinks === 0) return {rows: [], hasNext: false};

    // Get header row to map columns
    const headerRow = best.querySelector("tr th") ? best.querySelector("tr")
                    : best.querySelector("thead tr");
    const headers = headerRow
        ? Array.from(headerRow.querySelectorAll("th,td")).map(h => h.innerText.trim().toLowerCase())
        : [];

    // Get data rows — skip header
    const dataRows = Array.from(best.querySelectorAll("tbody tr, tr")).filter(r => {
        const tds = r.querySelectorAll("td");
        return tds.length >= 4 && !r.querySelector("th");
    });

    const rows = dataRows.map(row => {
        const cells = Array.from(row.querySelectorAll("td"));
        const links = Array.from(row.querySelectorAll("a")).filter(a => a.href && !a.href.includes("login"));
        const titleLink = links.find(a => a.href.includes("tender") || a.href.includes("view") || a.innerText.trim().length > 5);

        return {
            ref_no:         cells[0] ? cells[0].innerText.trim() : "",
            title:          titleLink ? titleLink.innerText.trim() : (cells[1] ? cells[1].innerText.trim() : ""),
            department:     cells[2] ? cells[2].innerText.trim() : "",
            published_date: cells[3] ? cells[3].innerText.trim() : "",
            closing_date:   cells[4] ? cells[4].innerText.trim() : "",
            detail_href:    titleLink ? titleLink.href : (links[0] ? links[0].href : ""),
        };
    }).filter(r => r.title || r.ref_no);

    // Check for "Next" pagination
    const nextLink = Array.from(document.querySelectorAll("a, input[type=submit]")).find(e => {
        const t = (e.innerText || e.value || "").trim().toLowerCase();
        return t === "next" || t === ">" || t === ">>" || t === "next page";
    });

    const totalText = document.body.innerText.match(/\\d+\\s*(?:tender|record|result)/i)?.[0] || "";

    return {rows, hasNext: !!nextLink, nextEl: nextLink ? (nextLink.id || nextLink.name || nextLink.className) : "", totalText};
}"""


class KarnatakaAgent(BaseAgent):
    """Scrapes Karnataka e-Procurement (JSF/Seam portal) — no CAPTCHA."""

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        days_back: int = 90,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.portal_id)
        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            log.info(f"[karnataka] Loading tender list...")
            await page.goto(TENDERS_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # Fill search form with date range
            today   = datetime.now()
            from_dt = today - timedelta(days=days_back)
            fmt     = "%d/%m/%Y"

            await page.evaluate(f"""() => {{
                const setV = (sel, v) => {{
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.value = v;
                    ['input','change','blur'].forEach(e => el.dispatchEvent(new Event(e, {{bubbles:true}})));
                }};
                setV("input[id*='tenderCreateDateFrom']", "{from_dt.strftime(fmt)}");
                setV("input[id*='tenderCreateDateTo']",   "{today.strftime(fmt)}");
            }}""")
            await page.wait_for_timeout(800)

            log.info(f"[karnataka] Searching: {from_dt.strftime(fmt)} → {today.strftime(fmt)}")

            # Submit search
            await page.click("input[name='eprocTenders:butSearch']")
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await page.wait_for_timeout(3000)

            current_page = 1
            while True:
                if max_pages and current_page > max_pages:
                    break

                data = await page.evaluate(EXTRACT_JS)
                rows = data.get("rows", [])

                if not rows:
                    hint = await page.evaluate("() => document.body.innerText.match(/no tender|no record/i)?.[0] || ''")
                    log.info(f"[karnataka] Page {current_page}: no rows ({hint})")
                    break

                tenders = [self._parse_row(r, current_page) for r in rows]
                if org_filter:
                    tenders = [t for t in tenders if org_filter.lower() in t["organisation"].lower()]

                result.tenders.extend(tenders)
                result.pages = current_page

                total_text = data.get("totalText", "")
                log.info(f"[karnataka] Page {current_page} — {len(tenders)} tenders {total_text}")

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                if not data.get("hasNext"):
                    break

                # Click next
                try:
                    async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                        await page.evaluate("""() => {
                            const next = Array.from(document.querySelectorAll("a,input")).find(e => {
                                const t = (e.innerText||e.value||"").trim().toLowerCase();
                                return t === "next" || t === ">" || t === ">>";
                            });
                            if (next) next.click();
                        }""")
                    current_page += 1
                    await page.wait_for_timeout(1500)
                except Exception:
                    break

                await random_delay(1.0, 2.5)

        except Exception as e:
            log.error(f"[karnataka] Error: {e}")
            result.errors.append(str(e))
        finally:
            await ctx.close()

        return result

    def _parse_row(self, row: dict, page_num: int) -> dict:
        title  = row.get("title", "")
        ref_no = row.get("ref_no", "")
        return {
            "portal_id":            "karnataka",
            "portal_name":          "Karnataka e-Procurement",
            "tender_id":            ref_no or title[:80],
            "ref_number":           ref_no,
            "title":                title,
            "organisation":         row.get("department", ""),
            "published_date":       row.get("published_date", ""),
            "closing_date":         row.get("closing_date", ""),
            "opening_date":         "",
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
