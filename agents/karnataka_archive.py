"""
Karnataka e-Procurement Archive/Awards Agent
Portal: https://eproc.karnataka.gov.in/eprocportal/pages/index.jsp
Platform: JSF/Seam — same portal, different status filter (Awarded / Closed)

Approach:
  - Navigate to eproc_tenders_list.seam
  - Fill date range (last N days → today)
  - Set status dropdown to AWARDED or CLOSED
  - Submit form, parse results table (includes awardee + pricing columns)
  - Paginate via "Next" if available
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("karnataka_archive")

TENDERS_URL = "https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam"

EXTRACT_ARCHIVE_JS = """() => {
    const allTables = Array.from(document.querySelectorAll("table"));
    let best = null, maxLinks = 0;
    for (const t of allTables) {
        const n = t.querySelectorAll("a[href*='tender'], a[href*='Tender'], a[href*='view']").length;
        if (n > maxLinks) { maxLinks = n; best = t; }
    }
    if (!best || maxLinks === 0) return {rows: [], hasNext: false};

    const headerRow = best.querySelector("tr th") ? best.querySelector("tr")
                    : best.querySelector("thead tr") || best.querySelector("tr");
    const headers = headerRow
        ? Array.from(headerRow.querySelectorAll("th,td")).map(h => h.innerText.trim().toLowerCase())
        : [];

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
            // Archive/awarded may have extra columns
            award_winner:   cells[5] ? cells[5].innerText.trim() : "",
            award_amount:   cells[6] ? cells[6].innerText.trim() : "",
            award_date:     cells[7] ? cells[7].innerText.trim() : "",
            detail_href:    titleLink ? titleLink.href : (links[0] ? links[0].href : ""),
            all_cells:      cells.map(c => c.innerText.trim()),
        };
    }).filter(r => r.title || r.ref_no);

    const nextLink = Array.from(document.querySelectorAll("a, input[type=submit]")).find(e => {
        const t = (e.innerText || e.value || "").trim().toLowerCase();
        return t === "next" || t === ">" || t === ">>" || t === "next page";
    });

    return {rows, hasNext: !!nextLink, headers};
}"""


class KarnatakaArchiveAgent(BaseAgent):
    """Scrapes Karnataka e-Procurement for closed/awarded tenders with pricing."""

    def __init__(self, config: PortalConfig, session: BrowserSession, scope: str = "archive"):
        super().__init__(config)
        self.session = session
        self.scope = scope

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        days_back: int = 365,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.portal_id)
        scopes = ["archive", "awards"] if self.scope in ("both", "all") else [self.scope]

        for sc in scopes:
            status_label = "Awarded" if sc == "awards" else "Closed"
            batch = await self._scrape_status(
                status_label, max_pages or 5, org_filter, progress_cb, days_back
            )
            for t in batch:
                t["status"] = status_label
            result.tenders.extend(batch)
            log.info(f"[karnataka_archive] {sc}: {len(batch)} tenders")

        return result

    async def _scrape_status(self, status_label, max_pages, org_filter, progress_cb, days_back):
        tenders = []
        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            log.info(f"[karnataka_archive] Loading for status={status_label}...")
            await page.goto(TENDERS_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # Fill date range
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
            await page.wait_for_timeout(500)

            # Set status dropdown to Awarded/Closed
            await page.evaluate(f"""() => {{
                const selects = Array.from(document.querySelectorAll("select"));
                for (const sel of selects) {{
                    const opts = Array.from(sel.options);
                    for (const opt of opts) {{
                        if (opt.text.toLowerCase().includes("{status_label.lower()}")) {{
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', {{bubbles:true}}));
                            return true;
                        }}
                    }}
                }}
                return false;
            }}""")
            await page.wait_for_timeout(800)

            log.info(f"[karnataka_archive] Searching: {from_dt.strftime(fmt)} → {today.strftime(fmt)}, status={status_label}")

            # Submit
            try:
                await page.click("input[name='eprocTenders:butSearch']")
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(3000)
            except Exception as e:
                log.warning(f"[karnataka_archive] Submit failed: {e}")
                await ctx.close()
                return tenders

            current_page = 1
            while True:
                if max_pages and current_page > max_pages:
                    break

                data = await page.evaluate(EXTRACT_ARCHIVE_JS)
                rows = data.get("rows", [])

                if not rows:
                    break

                for r in rows:
                    t = self._parse_row(r, current_page, status_label, data.get("headers", []))
                    if org_filter and org_filter.lower() not in t["organisation"].lower():
                        continue
                    tenders.append(t)

                result_pages = current_page
                log.info(f"[karnataka_archive] Page {current_page} — {len(rows)} tenders")

                if progress_cb:
                    await progress_cb(current_page, len(tenders))

                if not data.get("hasNext"):
                    break

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
            log.error(f"[karnataka_archive] Error: {e}")
        finally:
            await ctx.close()

        return tenders

    def _parse_row(self, row: dict, page_num: int, status: str, headers: list) -> dict:
        title  = row.get("title", "")
        ref_no = row.get("ref_no", "")
        cells  = row.get("all_cells", [])

        # Try to find award/pricing info from extra columns using header heuristics
        award_winner = row.get("award_winner", "")
        award_amount = row.get("award_amount", "")
        award_date   = row.get("award_date", "")

        # If headers available, do smarter mapping
        for i, h in enumerate(headers):
            if i < len(cells):
                hl = h.lower()
                if any(k in hl for k in ["award", "winner", "contractor", "bidder"]):
                    award_winner = award_winner or cells[i]
                elif any(k in hl for k in ["amount", "value", "price", "cost"]):
                    award_amount = award_amount or cells[i]
                elif any(k in hl for k in ["award date", "aoc date"]):
                    award_date = award_date or cells[i]

        return {
            "portal_id":            self.portal_id,
            "portal_name":          "Karnataka e-Procurement",
            "tender_id":            ref_no or title[:80],
            "ref_number":           ref_no,
            "title":                title,
            "organisation":         row.get("department", ""),
            "published_date":       row.get("published_date", ""),
            "closing_date":         row.get("closing_date", ""),
            "opening_date":         "",
            "status":               status,
            "detail_url":           row.get("detail_href", ""),
            "scraped_at":           now_iso(),
            "page_num":             page_num,
            "detail_scraped":       False,
            "tender_value_inr":     award_amount,
            "tender_fee_inr":       "",
            "emd_inr":              "",
            "tender_type":          "",
            "location":             "Karnataka",
            "award_winner":         award_winner,
            "award_date":           award_date,
            "award_amount":         award_amount,
        }
