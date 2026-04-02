"""
Generic Agent — auto-detect tender listings from ANY layout:
  - HTML tables (original)
  - Card/div layouts (BidAssist, TenderKart, TheTenders, etc.)
  - List items (IndianTenders, data.gov.in)
  - SPA rendered content

Enhanced to capture: pricing, EMD, awardee, award amount, tender fee.
Uses heuristic column/field mapping + debug screenshots.
"""
from __future__ import annotations
import logging
from pathlib import Path
from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("generic")

# ── Strategy 1: Table extraction (original, works for classic gov portals) ───
TABLE_JS = """() => {
    const tables = Array.from(document.querySelectorAll('table'));
    let best = null, maxRows = 0;
    for (const t of tables) {
        const n = t.querySelectorAll('tr').length;
        if (n > maxRows) { maxRows = n; best = t; }
    }
    if (!best || maxRows < 3) return null;

    const headerCells = Array.from((best.querySelector('tr th') ? best.querySelector('tr') : best.querySelector('thead tr') || best.querySelector('tr'))?.querySelectorAll('th, td') || []);
    const headers = headerCells.map(c => c.innerText.trim().toLowerCase());

    const dataRows = Array.from(best.querySelectorAll('tbody tr, tr')).filter(r => !r.querySelector('th'));
    const rows = dataRows.slice(0, 500).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const link  = row.querySelector('a');
        return {
            cells:       cells.map(c => c.innerText.trim().replace(/\\s+/g, ' ')),
            detail_href: link ? link.href : ''
        };
    }).filter(r => r.cells.some(c => c && c.length > 1));

    return { type: 'table', rows, headers, rowCount: rows.length };
}"""

# ── Strategy 2: Card/div/list extraction (SPA sites, modern layouts) ─────────
CARD_JS = """() => {
    // Look for repeated card/item containers
    const cardSelectors = [
        '.tender-card', '.tender-item', '.tender-row', '.tender-list-item',
        '.bid-card', '.bid-item', '.bid-row',
        '.list-group-item', '.card', '.item-card',
        '[class*="tender"][class*="card"]', '[class*="tender"][class*="item"]',
        '[class*="tender"][class*="list"]', '[class*="bid"][class*="card"]',
        '.search-result', '.result-item', '.listing-item',
        'article', '.post', '.entry',
        // Generic repeated containers with links
        'div.row > div[class*="col"]',
    ];

    for (const sel of cardSelectors) {
        const items = document.querySelectorAll(sel);
        if (items.length >= 3) {
            const rows = Array.from(items).slice(0, 200).map(item => {
                const text = item.innerText.trim();
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                const link = item.querySelector('a[href]');
                return {
                    text: text,
                    lines: lines,
                    detail_href: link ? link.href : '',
                };
            }).filter(r => r.lines.length >= 2);

            if (rows.length >= 2) {
                return { type: 'cards', rows, selector: sel, count: rows.length };
            }
        }
    }

    // Fallback: find any container with 5+ child divs that each contain links
    const containers = document.querySelectorAll('div, section, ul');
    for (const container of containers) {
        const children = Array.from(container.children);
        if (children.length < 5 || children.length > 500) continue;

        const withLinks = children.filter(c => c.querySelector('a') && c.innerText.trim().length > 30);
        if (withLinks.length >= 5) {
            const rows = withLinks.slice(0, 200).map(item => {
                const text = item.innerText.trim();
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                const link = item.querySelector('a[href]');
                return {
                    text: text,
                    lines: lines,
                    detail_href: link ? link.href : '',
                };
            });
            if (rows.length >= 3) {
                return { type: 'divs', rows, count: rows.length };
            }
        }
    }

    return null;
}"""

# ── Strategy 3: Extract ALL links that look like tender detail pages ─────────
LINKS_JS = """() => {
    const links = Array.from(document.querySelectorAll('a[href]'));
    const tenderLinks = links.filter(a => {
        const href = (a.href || '').toLowerCase();
        const text = (a.innerText || '').trim();
        return text.length > 15 && text.length < 500 &&
               (href.includes('tender') || href.includes('bid') || href.includes('detail') ||
                href.includes('view') || href.includes('notice') || href.includes('procurement'));
    });

    if (tenderLinks.length < 3) return null;

    const rows = tenderLinks.slice(0, 100).map(a => {
        // Get parent container text for context
        const parent = a.closest('tr, li, div, article, .card, .item') || a.parentElement;
        const context = parent ? parent.innerText.trim() : a.innerText.trim();
        const lines = context.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
        return {
            text: context,
            lines: lines,
            title: a.innerText.trim(),
            detail_href: a.href,
        };
    });

    return { type: 'links', rows, count: rows.length };
}"""

NEXT_JS = """() => {
    const btns = [
        document.querySelector('a[title*="Next"], a[title*="next"]'),
        document.querySelector('a.next, a.nextPage, a[rel="next"]'),
        document.querySelector('input[value=">"], input[value="Next"]'),
        document.querySelector('[aria-label="Next"], [aria-label="next page"]'),
        document.querySelector('.pagination .next a, .pager .next a'),
        Array.from(document.querySelectorAll('a')).find(a =>
            ['>', '>>', 'next', 'next page'].includes(a.innerText.trim().toLowerCase())
        ),
    ].filter(Boolean);
    if (!btns.length) return null;
    const b = btns[0];
    return b.id || b.className || b.href || b.innerText.trim();
}"""

HEURISTIC_MAP = [
    (["id","no","number","sr","ref","nit"],                    "tender_id"),
    (["title","description","work","subject","name"],           "title"),
    (["org","department","ministry","authority","buyer"],        "organisation"),
    (["published","issue","start","from","posted"],             "published_date"),
    (["clos","end","due","last","deadline","bid end"],          "closing_date"),
    (["open","open date","bid open"],                           "opening_date"),
    (["value","amount","cost","estimate","budget","₹","rs"],    "tender_value_inr"),
    (["emd","earnest","security deposit"],                      "emd_inr"),
    (["fee","document fee","tender fee"],                       "tender_fee_inr"),
    (["location","district","state","city","place"],            "location"),
    (["type","category","work type"],                           "tender_type"),
    (["award","winner","contractor","awarded to","bidder"],     "award_winner"),
    (["award amount","contract value","award value"],           "award_amount"),
    (["award date","aoc date"],                                 "award_date"),
]


class GenericAgent(BaseAgent):

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
        url = getattr(self.config, 'results_url', '') or self.config.base_url
        if not url:
            result.skipped = True
            result.skip_reason = "No URL configured"
            return result

        ctx  = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            log.info(f"[{self.portal_id}] Loading: {url}")
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # Scroll down to trigger lazy loads
            for _ in range(3):
                await page.mouse.wheel(0, 600)
                await page.wait_for_timeout(500)

            # Save debug screenshot
            ss_path = Path("screenshots") / f"{self.portal_id}_debug.png"
            await page.screenshot(path=str(ss_path), full_page=True)
            log.info(f"[{self.portal_id}] Debug screenshot: {ss_path}")

            current_page = 1
            while True:
                if max_pages and current_page > max_pages:
                    break

                # Try extraction strategies in order: table → cards → links
                data = await page.evaluate(TABLE_JS)
                strategy = "table"

                if not data or not data.get("rows"):
                    data = await page.evaluate(CARD_JS)
                    strategy = "cards"

                if not data or not data.get("rows"):
                    data = await page.evaluate(LINKS_JS)
                    strategy = "links"

                if not data or not data.get("rows"):
                    log.warning(f"[{self.portal_id}] Page {current_page}: No content found (tried table/cards/links)")
                    result.skip_reason = "No tender listings detected"
                    break

                rows = data.get("rows", [])
                data_type = data.get("type", strategy)

                if data_type == "table":
                    headers = data.get("headers", [])
                    tenders = [self._parse_table_row(r, headers, current_page) for r in rows]
                else:
                    tenders = [self._parse_card_row(r, current_page) for r in rows]

                # Filter out empty/junk rows
                tenders = [t for t in tenders if t.get("title") and len(t["title"]) > 5]

                if org_filter:
                    tenders = [t for t in tenders if org_filter.lower() in t.get("organisation","").lower()]

                result.tenders.extend(tenders)
                result.pages = current_page
                log.info(f"[{self.portal_id}] Page {current_page} — {len(tenders)} rows via {data_type}")

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                next_info = await page.evaluate(NEXT_JS)
                if not next_info:
                    break

                await random_delay()
                try:
                    async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                        await page.evaluate("""() => {
                            const b = document.querySelector('a[title*="Next"], a.next, a.nextPage, [aria-label="Next"]')
                                   || document.querySelector('.pagination .next a, .pager .next a')
                                   || Array.from(document.querySelectorAll('a')).find(a => ['>', 'next'].includes(a.innerText.trim().toLowerCase()));
                            if (b) b.click();
                        }""")
                    current_page += 1
                except Exception:
                    break

        except Exception as e:
            log.error(f"[{self.portal_id}] Error: {e}")
            try:
                await page.screenshot(path=f"screenshots/{self.portal_id}_error.png")
            except Exception:
                pass
            result.errors.append(str(e))
            result.skip_reason = str(e)[:200]
        finally:
            await ctx.close()

        return result

    def _parse_table_row(self, row: dict, headers: list, page_num: int) -> dict:
        """Parse a table row using header heuristics."""
        cells = row.get("cells", [])
        hmap = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))} if headers else {}
        for i, c in enumerate(cells):
            hmap[f"col_{i}"] = c

        def find(*patterns) -> str:
            for pat in patterns:
                for k, v in hmap.items():
                    if any(p in k for p in pat) and v:
                        return v
            return ""

        mapped: dict[str, str] = {}
        for patterns, field in HEURISTIC_MAP:
            mapped[field] = find(patterns)

        def c(i): return cells[i] if i < len(cells) else ""

        status = getattr(self, '_forced_status', 'Active')

        return {
            "portal_id":            self.portal_id,
            "portal_name":          self.config.display_name,
            "source_website":       self.config.base_url,
            "tender_id":            mapped.get("tender_id") or c(0),
            "ref_number":           mapped.get("tender_id") or c(0),
            "title":                mapped.get("title") or c(1),
            "organisation":         mapped.get("organisation") or c(2),
            "published_date":       mapped.get("published_date") or c(3),
            "closing_date":         mapped.get("closing_date") or c(4),
            "opening_date":         mapped.get("opening_date") or "",
            "status":               status,
            "detail_url":           row.get("detail_href", ""),
            "scraped_at":           now_iso(),
            "page_num":             page_num,
            "detail_scraped":       False,
            "tender_value_inr":     mapped.get("tender_value_inr", ""),
            "tender_fee_inr":       mapped.get("tender_fee_inr", ""),
            "emd_inr":              mapped.get("emd_inr", ""),
            "tender_type":          mapped.get("tender_type", ""),
            "tender_category":      "",
            "product_category":     "",
            "form_of_contract":     "",
            "payment_mode":         "",
            "bid_submission_start": "",
            "bid_submission_end":   "",
            "doc_download_start":   "",
            "doc_download_end":     "",
            "location":             mapped.get("location", ""),
            "pincode":              "",
            "contact":              "",
            "documents":            "",
            "award_winner":         mapped.get("award_winner", ""),
            "award_date":           mapped.get("award_date", ""),
            "award_amount":         mapped.get("award_amount", ""),
        }

    def _parse_card_row(self, row: dict, page_num: int) -> dict:
        """Parse a card/div/link-based row using text line heuristics."""
        import re

        lines = row.get("lines", [])
        text = row.get("text", "")
        title = row.get("title", "") or (lines[0] if lines else "")

        # Extract fields from text using regex patterns
        def extract(pattern, source=text):
            m = re.search(pattern, source, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        org = extract(r"(?:Organisation|Department|Ministry|Authority|Buyer|Agency)[:\s]+([^\n]+)")
        closing = extract(r"(?:Closing|Due|Bid End|Last Date|Deadline)[:\s]+([^\n]+)")
        published = extract(r"(?:Published|Posted|Start|Issue|Tender Date)[:\s]+([^\n]+)")
        value = extract(r"(?:Value|Amount|Estimate|Budget|Cost|₹|Rs\.?)[:\s]*([\d,\.]+\s*(?:Cr|L|Lakh|crore|Lakhs)?)")
        emd = extract(r"(?:EMD|Earnest Money|Security Deposit)[:\s]*([\d,\.]+)")
        fee = extract(r"(?:Tender Fee|Document Fee|Cost of Document)[:\s]*([\d,\.]+)")
        location = extract(r"(?:Location|Place|City|District|State)[:\s]+([^\n]+)")
        ref = extract(r"(?:Ref|Reference|NIT|Tender No|ID)[:\s]+([^\n]+)")
        tender_type = extract(r"(?:Type|Category|Work Type)[:\s]+([^\n]+)")
        award_winner = extract(r"(?:Awarded To|Winner|Contractor|Bidder)[:\s]+([^\n]+)")
        award_amount = extract(r"(?:Award Amount|Contract Value|Award Value)[:\s]*([\d,\.]+)")
        award_date = extract(r"(?:Award Date|AOC Date)[:\s]+([^\n]+)")

        # Fallback: try to extract org from lines
        if not org and len(lines) > 1:
            for line in lines[1:4]:
                ll = line.lower()
                if any(k in ll for k in ["department", "ministry", "authority", "organisation", "corp", "ltd"]):
                    org = line.split(":")[-1].strip() if ":" in line else line
                    break

        # Fallback: try to find dates in lines
        date_pattern = r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}'
        if not closing:
            for line in reversed(lines):
                m = re.search(date_pattern, line)
                if m:
                    closing = m.group()
                    break

        status = getattr(self, '_forced_status', 'Active')

        return {
            "portal_id":            self.portal_id,
            "portal_name":          self.config.display_name,
            "source_website":       self.config.base_url,
            "tender_id":            ref or (row.get("detail_href", "").split("/")[-1] if row.get("detail_href") else ""),
            "ref_number":           ref,
            "title":                title[:300],
            "organisation":         org,
            "published_date":       published,
            "closing_date":         closing,
            "opening_date":         "",
            "status":               status,
            "detail_url":           row.get("detail_href", ""),
            "scraped_at":           now_iso(),
            "page_num":             page_num,
            "detail_scraped":       False,
            "tender_value_inr":     value,
            "tender_fee_inr":       fee,
            "emd_inr":              emd,
            "tender_type":          tender_type,
            "tender_category":      "",
            "product_category":     "",
            "form_of_contract":     "",
            "payment_mode":         "",
            "bid_submission_start": "",
            "bid_submission_end":   "",
            "doc_download_start":   "",
            "doc_download_end":     "",
            "location":             location,
            "pincode":              "",
            "contact":              "",
            "documents":            "",
            "award_winner":         award_winner,
            "award_date":           award_date,
            "award_amount":         award_amount,
        }
