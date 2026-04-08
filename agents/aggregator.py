"""
Tender Aggregator Agents
========================
Scrapes commercial tender aggregation websites that index Indian government
tenders from multiple portals. These sites typically have simple HTML table or
card layouts since they are built for SEO and public discovery.

Portals covered:
    tenderdetail   - tenderdetail.com       (national aggregator, table layout)
    tendertiger    - tendertiger.com         (national aggregator, table/card layout)
    tenderkart     - tenderkart.in           (aggregator with category browsing)
    tender247      - tender247.com           (aggregator, tabular listings)
    gujarattenders - gujarattenders.in       (Gujarat-specific aggregator)
    palladium      - app.palladium.primenumbers.in (SPA, API interception)

Each agent extends BaseAgent and follows the same scrape() contract used across
the project.  Browser automation via Playwright + BrowserSession from core.browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso
from portals.configs import PortalConfig

log = logging.getLogger("aggregator")
SCREENSHOTS_DIR = Path("screenshots")

# ---------------------------------------------------------------------------
# Portal configs (importable by main.py without touching portals/configs.py)
# ---------------------------------------------------------------------------

TENDERDETAIL_CONFIG = PortalConfig(
    portal_id="tenderdetail",
    display_name="TenderDetail.com",
    base_url="https://www.tenderdetail.com",
    platform="generic",
    category="Info",
    results_url="https://www.tenderdetail.com/",
    notes="Commercial aggregator with latest tenders table",
)

TENDERTIGER_CONFIG = PortalConfig(
    portal_id="tendertiger",
    display_name="TenderTiger.com",
    base_url="https://www.tendertiger.com",
    platform="generic",
    category="Info",
    results_url="https://www.tendertiger.com/",
    notes="National tender aggregator with card/table listings",
)

TENDERKART_CONFIG = PortalConfig(
    portal_id="tenderkart",
    display_name="TenderKart.in",
    base_url="https://www.tenderkart.in",
    platform="generic",
    category="Info",
    results_url="https://www.tenderkart.in",
    notes="Tender aggregator with category-based browsing",
)

TENDER247_CONFIG = PortalConfig(
    portal_id="tender247",
    display_name="Tender247.com",
    base_url="https://tender247.com",
    platform="generic",
    category="Info",
    results_url="https://tender247.com",
    notes="Aggregator with tabular tender listings",
)

GUJARATTENDERS_CONFIG = PortalConfig(
    portal_id="gujarattenders",
    display_name="GujaratTenders.in",
    base_url="https://gujarattenders.in",
    platform="generic",
    category="State",
    results_url="https://gujarattenders.in",
    notes="Gujarat-specific tender aggregator",
)

PALLADIUM_CONFIG = PortalConfig(
    portal_id="palladium",
    display_name="Palladium (PrimeNumbers)",
    base_url="https://app.palladium.primenumbers.in",
    platform="generic",
    category="Info",
    results_url="https://app.palladium.primenumbers.in",
    notes="Angular/React SPA - uses API interception for data extraction",
)

AGGREGATOR_CONFIGS: dict[str, PortalConfig] = {
    cfg.portal_id: cfg for cfg in [
        TENDERDETAIL_CONFIG,
        TENDERTIGER_CONFIG,
        TENDERKART_CONFIG,
        TENDER247_CONFIG,
        GUJARATTENDERS_CONFIG,
        PALLADIUM_CONFIG,
    ]
}

# ---------------------------------------------------------------------------
# JS helpers shared across agents
# ---------------------------------------------------------------------------

# Extract the largest table on the page into structured rows
AUTO_TABLE_JS = """() => {
    const tables = Array.from(document.querySelectorAll('table'));
    let best = null, maxRows = 0;
    for (const t of tables) {
        const n = t.querySelectorAll('tr').length;
        if (n > maxRows) { maxRows = n; best = t; }
    }
    if (!best || maxRows < 2) return { rows: [], headers: [], note: 'No suitable table found' };

    const hdrRow = best.querySelector('thead tr') || best.querySelector('tr');
    const headerCells = hdrRow ? Array.from(hdrRow.querySelectorAll('th, td')) : [];
    const headers = headerCells.map(c => c.innerText.trim().toLowerCase().replace(/\\s+/g, ' '));

    const allRows = Array.from(best.querySelectorAll('tbody tr, tr'));
    const dataRows = allRows.filter(r => !r.querySelector('th') || r.querySelectorAll('td').length > 0);
    const rows = dataRows.slice(0, 500).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const links = Array.from(row.querySelectorAll('a[href]'));
        const firstLink = links.length ? links[0].href : '';
        return {
            cells: cells.map(c => c.innerText.trim().replace(/\\s+/g, ' ')),
            detail_href: firstLink,
            all_links: links.map(a => ({text: a.innerText.trim(), href: a.href}))
        };
    }).filter(r => r.cells.length > 0 && r.cells.some(c => c.length > 1));

    return { rows, headers, rowCount: rows.length };
}"""

# Extract card-based listings (div.card, article, li with links)
AUTO_CARDS_JS = """() => {
    // Try multiple card selectors common on aggregator sites
    const selectors = [
        '.tender-list .tender-item, .tender-list li',
        '.card, .tender-card, .tender-box',
        'article, .post, .listing-item',
        '.result-item, .search-result',
        'div[class*="tender"], div[class*="Tender"]',
        '.table-responsive tr, .data-table tr',
    ];
    let items = [];
    for (const sel of selectors) {
        items = Array.from(document.querySelectorAll(sel));
        if (items.length >= 2) break;
    }
    if (items.length < 1) return { cards: [], note: 'No card elements found' };

    return {
        cards: items.slice(0, 200).map(el => {
            const link = el.querySelector('a[href]');
            const allText = el.innerText.trim().replace(/\\s+/g, ' ');
            // Try to extract structured fields from the card
            const title = (el.querySelector('h2, h3, h4, h5, .title, .tender-title, a') || {}).innerText || '';
            const org = (el.querySelector('.org, .organisation, .department, .company') || {}).innerText || '';
            const date = (el.querySelector('.date, .closing-date, time, .deadline') || {}).innerText || '';
            const location = (el.querySelector('.location, .state, .city, .region') || {}).innerText || '';
            const value = (el.querySelector('.value, .amount, .cost, .estimate') || {}).innerText || '';
            return {
                text: allText.slice(0, 1000),
                title: title.trim(),
                org: org.trim(),
                date: date.trim(),
                location: location.trim(),
                value: value.trim(),
                href: link ? link.href : '',
            };
        }),
        count: items.length,
    };
}"""

# Generic next-page detection
NEXT_PAGE_JS = """() => {
    const candidates = [
        document.querySelector('a[title*="Next" i], a[title*="next" i]'),
        document.querySelector('a.next, a.nextPage, a[rel="next"], a.page-link[aria-label="Next"]'),
        document.querySelector('li.next a, li.pagination-next a'),
        document.querySelector('input[value=">"], input[value="Next"]'),
        Array.from(document.querySelectorAll('a, button')).find(a => {
            const t = a.innerText.trim().toLowerCase();
            return ['next', 'next page', '>', '>>', 'next >', 'next >>'].includes(t);
        }),
    ].filter(Boolean);
    if (!candidates.length) return null;
    const el = candidates[0];
    if (el.classList.contains('disabled') || el.getAttribute('aria-disabled') === 'true') return null;
    return { tag: el.tagName, id: el.id, cls: el.className, href: el.href || '', text: el.innerText.trim() };
}"""

# Click the next-page element
CLICK_NEXT_JS = """() => {
    const candidates = [
        document.querySelector('a[title*="Next" i], a[title*="next" i]'),
        document.querySelector('a.next, a.nextPage, a[rel="next"], a.page-link[aria-label="Next"]'),
        document.querySelector('li.next a, li.pagination-next a'),
        document.querySelector('input[value=">"], input[value="Next"]'),
        Array.from(document.querySelectorAll('a, button')).find(a => {
            const t = a.innerText.trim().toLowerCase();
            return ['next', 'next page', '>', '>>', 'next >', 'next >>'].includes(t);
        }),
    ].filter(Boolean);
    if (!candidates.length) return false;
    const el = candidates[0];
    if (el.classList.contains('disabled') || el.getAttribute('aria-disabled') === 'true') return false;
    el.click();
    return true;
}"""


# ---------------------------------------------------------------------------
# Heuristic field mapping (shared)
# ---------------------------------------------------------------------------

FIELD_PATTERNS = [
    (["id", "no", "number", "sr", "sl", "ref", "tender no", "tender id"],  "ref_number"),
    (["title", "description", "work", "subject", "name", "particular"],    "title"),
    (["org", "department", "ministry", "authority", "agency", "buyer"],     "organisation"),
    (["publish", "issue", "start", "from", "posted"],                      "published_date"),
    (["clos", "end", "due", "last", "deadline", "submission"],             "closing_date"),
    (["open", "open date", "opening"],                                     "opening_date"),
    (["value", "amount", "cost", "estimate", "budget"],                    "tender_value_inr"),
    (["emd", "earnest"],                                                   "emd_inr"),
    (["location", "district", "state", "city", "region", "place"],         "location"),
    (["type", "category", "nature"],                                       "tender_type"),
    (["status"],                                                           "status"),
]


def _heuristic_map(headers: list[str], cells: list[str]) -> dict[str, str]:
    """Map cell values to standard fields using fuzzy header matching."""
    hmap = {}
    for i, h in enumerate(headers):
        if i < len(cells):
            hmap[h] = cells[i]

    result: dict[str, str] = {}
    for patterns, field_name in FIELD_PATTERNS:
        for pat in patterns:
            for hdr, val in hmap.items():
                if pat in hdr and val:
                    result[field_name] = val
                    break
            if field_name in result:
                break
    return result


def _build_tender(
    portal_id: str,
    portal_name: str,
    *,
    ref_number: str = "",
    title: str = "",
    organisation: str = "",
    published_date: str = "",
    closing_date: str = "",
    opening_date: str = "",
    tender_value_inr: str = "",
    location: str = "",
    tender_type: str = "",
    status: str = "Active",
    detail_url: str = "",
    page_num: int = 1,
    extra: dict | None = None,
) -> dict:
    """Build a standardised tender dict."""
    tender = {
        "portal_id": portal_id,
        "portal_name": portal_name,
        "tender_id": ref_number or title[:60],
        "ref_number": ref_number,
        "title": title,
        "organisation": organisation,
        "published_date": published_date,
        "closing_date": closing_date,
        "opening_date": opening_date,
        "status": status,
        "detail_url": detail_url,
        "scraped_at": now_iso(),
        "page_num": page_num,
        "detail_scraped": False,
        "tender_value_inr": tender_value_inr,
        "tender_fee_inr": "",
        "emd_inr": "",
        "tender_type": tender_type,
        "tender_category": "",
        "product_category": "",
        "form_of_contract": "",
        "payment_mode": "",
        "bid_submission_start": "",
        "bid_submission_end": "",
        "doc_download_start": "",
        "doc_download_end": "",
        "location": location,
        "pincode": "",
        "contact": "",
        "documents": "",
    }
    if extra:
        tender.update(extra)
    return tender


# ---------------------------------------------------------------------------
# Base mixin with shared pagination logic
# ---------------------------------------------------------------------------

class _AggregatorBase(BaseAgent):
    """Shared logic for table/card aggregator sites."""

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def _paginated_scrape(
        self,
        start_url: str,
        *,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        save_callback=None,
        progress_cb=None,
        parse_fn=None,
        wait_until: str = "networkidle",
        load_timeout: int = 60_000,
        post_load_wait: int = 3000,
    ) -> ScrapeResult:
        """
        Generic paginated scrape.  Navigates to start_url, extracts tenders
        from each page using parse_fn, clicks Next, and repeats.

        Args:
            parse_fn: async callable(page, page_num) -> list[dict]
                      If None, uses _extract_tenders_auto().
        """
        result = ScrapeResult(portal_id=self.portal_id)
        ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)

        try:
            log.info(f"[{self.portal_id}] Loading {start_url}")
            await page.goto(start_url, wait_until=wait_until, timeout=load_timeout)
            await page.wait_for_timeout(post_load_wait)

            # Debug screenshot
            SCREENSHOTS_DIR.mkdir(exist_ok=True)
            ss = SCREENSHOTS_DIR / f"{self.portal_id}_debug.png"
            try:
                await page.screenshot(path=str(ss), full_page=True)
            except Exception:
                pass

            current_page = 1
            while True:
                if max_pages and current_page > max_pages:
                    break

                # Extract tenders from current page
                extractor = parse_fn or self._extract_tenders_auto
                tenders = await extractor(page, current_page)

                if not tenders and current_page == 1:
                    log.warning(f"[{self.portal_id}] No tenders found on first page")
                    result.skip_reason = "No tenders found"

                if not tenders:
                    break

                # Apply org filter
                if org_filter:
                    tenders = [
                        t for t in tenders
                        if org_filter.lower() in t.get("organisation", "").lower()
                    ]

                result.tenders.extend(tenders)
                result.pages = current_page
                log.info(
                    f"[{self.portal_id}] Page {current_page}: "
                    f"{len(tenders)} tenders (total: {len(result.tenders)})"
                )

                # Incremental save
                if save_callback:
                    try:
                        save_callback(self.portal_id, result.tenders)
                    except Exception as e:
                        log.warning(f"[{self.portal_id}] save_callback error: {e}")

                if progress_cb:
                    await progress_cb(current_page, len(result.tenders))

                # Try to navigate to next page
                has_next = await page.evaluate(NEXT_PAGE_JS)
                if not has_next:
                    log.info(f"[{self.portal_id}] No next page found, done.")
                    break

                await random_delay(1.5, 3.5)
                try:
                    clicked = await page.evaluate(CLICK_NEXT_JS)
                    if not clicked:
                        break
                    # Wait for navigation or DOM update
                    await page.wait_for_load_state("networkidle", timeout=30_000)
                    await page.wait_for_timeout(2000)
                    current_page += 1
                except Exception as nav_err:
                    log.warning(f"[{self.portal_id}] Pagination failed: {nav_err}")
                    break

        except Exception as e:
            log.error(f"[{self.portal_id}] Error: {e}", exc_info=True)
            result.errors.append(str(e))
            try:
                await page.screenshot(
                    path=str(SCREENSHOTS_DIR / f"{self.portal_id}_error.png")
                )
            except Exception:
                pass
        finally:
            await ctx.close()

        log.info(
            f"[{self.portal_id}] Done: {len(result.tenders)} tenders "
            f"across {result.pages} pages"
        )
        return result

    async def _extract_tenders_auto(self, page, page_num: int) -> list[dict]:
        """Auto-detect tables or cards and extract tenders."""
        # Try table first
        table_data = await page.evaluate(AUTO_TABLE_JS)
        rows = table_data.get("rows", [])
        headers = table_data.get("headers", [])

        if rows:
            return self._parse_table_rows(rows, headers, page_num)

        # Fall back to card layout
        card_data = await page.evaluate(AUTO_CARDS_JS)
        cards = card_data.get("cards", [])

        if cards:
            return self._parse_cards(cards, page_num)

        return []

    def _parse_table_rows(
        self, rows: list[dict], headers: list[str], page_num: int
    ) -> list[dict]:
        """Parse rows extracted by AUTO_TABLE_JS into standardised tender dicts."""
        tenders = []
        for row in rows:
            cells = row.get("cells", [])
            if not cells or all(not c for c in cells):
                continue

            mapped = _heuristic_map(headers, cells)

            # Positional fallbacks when headers are missing or unhelpful
            def c(i):
                return cells[i].strip() if i < len(cells) else ""

            tender = _build_tender(
                self.portal_id,
                self.config.display_name,
                ref_number=mapped.get("ref_number", "") or c(0),
                title=mapped.get("title", "") or c(1),
                organisation=mapped.get("organisation", "") or c(2),
                published_date=mapped.get("published_date", ""),
                closing_date=mapped.get("closing_date", ""),
                opening_date=mapped.get("opening_date", ""),
                tender_value_inr=mapped.get("tender_value_inr", ""),
                location=mapped.get("location", ""),
                tender_type=mapped.get("tender_type", ""),
                status=mapped.get("status", "Active"),
                detail_url=row.get("detail_href", ""),
                page_num=page_num,
            )
            tenders.append(tender)
        return tenders

    def _parse_cards(self, cards: list[dict], page_num: int) -> list[dict]:
        """Parse cards extracted by AUTO_CARDS_JS into standardised tender dicts."""
        tenders = []
        for card in cards:
            text = card.get("text", "")
            if len(text) < 10:
                continue

            title = card.get("title", "") or text[:120]

            # Try to pull a date from the text using regex
            date_match = re.search(
                r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})', text
            )
            closing_date = date_match.group(1) if date_match else card.get("date", "")

            tender = _build_tender(
                self.portal_id,
                self.config.display_name,
                title=title,
                organisation=card.get("org", ""),
                closing_date=closing_date,
                location=card.get("location", ""),
                tender_value_inr=card.get("value", ""),
                detail_url=card.get("href", ""),
                page_num=page_num,
            )
            tenders.append(tender)
        return tenders


# ---------------------------------------------------------------------------
# TenderDetail.com
# ---------------------------------------------------------------------------

# Site-specific JS: tenderdetail.com lists tenders in a table with specific columns
TENDERDETAIL_JS = """() => {
    // TenderDetail.com structure:
    //   .col-sm-12 container
    //     h2.workDesc → org + location (e.g. "Local Bodies - ballia - Uttar Pradesh")
    //     a.m-brief → tender title link, contains span.m-tender-id
    //     .month/.day/.year spans → due date
    //     "Tender Value : X" text → value
    const containers = Array.from(document.querySelectorAll('.col-sm-12')).filter(el =>
        el.querySelector('a.m-brief') || el.querySelector('a[href*="TenderNotice"]')
    );

    if (containers.length) {
        return {
            type: 'structured',
            items: containers.slice(0, 200).map(el => {
                // Organisation from .workDesc
                const orgEl = el.querySelector('.workDesc');
                const org = orgEl ? orgEl.innerText.trim().replace(/\\s+/g, ' ') : '';

                // Tender title and ID from a.m-brief
                const briefLink = el.querySelector('a.m-brief') || el.querySelector('a[href*="TenderNotice"]');
                const idEl = el.querySelector('.m-tender-id');
                const tenderId = idEl ? idEl.innerText.trim() : '';
                let title = briefLink ? briefLink.getAttribute('title') || briefLink.innerText.trim() : '';
                // Clean ID prefix from title text
                if (tenderId && title.startsWith(tenderId)) {
                    title = title.slice(tenderId.length).trim();
                }
                title = title.replace(/^\\d+\\s*/, '').replace(/\\s+/g, ' ');

                // Due date from .month/.day/.year spans
                const monthEl = el.querySelector('.month');
                const dayEl = el.querySelector('.day');
                const yearEl = el.querySelector('.year');
                const dueDate = monthEl && dayEl && yearEl
                    ? (monthEl.innerText.trim() + ' ' + dayEl.innerText.trim() + ', ' + yearEl.innerText.trim())
                    : '';

                // Tender value
                const allText = el.innerText || '';
                const valMatch = allText.match(/Tender Value\\s*:\\s*([^\\n]+)/i);
                const value = valMatch ? valMatch[1].trim() : '';

                const href = briefLink ? briefLink.href : '';

                return { tenderId, title, org, dueDate, value, href };
            })
        };
    }

    // Fallback: try table layout
    const rows = Array.from(document.querySelectorAll(
        'table.table tr, table tbody tr'
    )).filter(r => r.querySelectorAll('td').length >= 3);

    if (rows.length) {
        const headerRow = document.querySelector('table thead tr, table tr:first-child');
        const headers = headerRow
            ? Array.from(headerRow.querySelectorAll('th, td')).map(c => c.innerText.trim().toLowerCase())
            : [];
        return {
            type: 'table',
            headers,
            items: rows.map(row => {
                const cells = Array.from(row.querySelectorAll('td'));
                const link = row.querySelector('a[href]');
                return {
                    cells: cells.map(c => c.innerText.trim().replace(/\\s+/g, ' ')),
                    href: link ? link.href : ''
                };
            })
        };
    }

    return { type: 'empty', items: [] };
}"""


class TenderDetailAgent(_AggregatorBase):
    """
    Scrapes tenderdetail.com -- one of the largest Indian tender aggregators.
    Lists tenders in paginated HTML tables with title, organisation, location,
    closing date, and tender value.
    """

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config, session)

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        save_callback=None,
    ) -> ScrapeResult:
        url = self.config.results_url or self.config.base_url
        return await self._paginated_scrape(
            url,
            max_pages=max_pages,
            org_filter=org_filter,
            fetch_details=fetch_details,
            save_callback=save_callback,
            progress_cb=progress_cb,
            parse_fn=self._parse_page,
        )

    async def _parse_page(self, page, page_num: int) -> list[dict]:
        """Extract tenders from tenderdetail.com page."""
        data = await page.evaluate(TENDERDETAIL_JS)

        if data.get("type") == "structured":
            tenders = []
            for item in data.get("items", []):
                title = item.get("title", "").strip()
                if not title:
                    continue
                tender = _build_tender(
                    self.portal_id,
                    self.config.display_name,
                    ref_number=item.get("tenderId", ""),
                    title=title,
                    organisation=item.get("org", ""),
                    closing_date=item.get("dueDate", ""),
                    tender_value_inr=item.get("value", ""),
                    detail_url=item.get("href", ""),
                    page_num=page_num,
                )
                tenders.append(tender)
            return tenders

        elif data.get("type") == "cards":
            return self._parse_cards(
                [{"text": i["text"], "title": i.get("title", ""), "href": i.get("href", ""),
                  "org": "", "date": "", "location": "", "value": ""}
                 for i in data.get("items", [])],
                page_num,
            )

        # Fallback to auto-detection
        return await self._extract_tenders_auto(page, page_num)


# ---------------------------------------------------------------------------
# TenderTiger.com
# ---------------------------------------------------------------------------

TENDERTIGER_JS = """() => {
    // TenderTiger often uses table layout or card divs
    const rows = Array.from(document.querySelectorAll(
        'table tr, .tender-row, .search-results tr'
    )).filter(r => r.querySelectorAll('td').length >= 2);

    if (rows.length) {
        const headerRow = document.querySelector('table thead tr, table tr:has(th)');
        const headers = headerRow
            ? Array.from(headerRow.querySelectorAll('th')).map(c => c.innerText.trim().toLowerCase())
            : [];
        return {
            type: 'table',
            headers,
            items: rows.map(r => ({
                cells: Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim().replace(/\\s+/g, ' ')),
                href: (r.querySelector('a[href]') || {}).href || ''
            }))
        };
    }

    // Card layout fallback
    const cards = Array.from(document.querySelectorAll(
        '.tender-item, .tender-card, .listing-item, .result-row, article, .card'
    ));
    return {
        type: cards.length ? 'cards' : 'empty',
        items: cards.slice(0, 200).map(c => ({
            text: c.innerText.trim().replace(/\\s+/g, ' ').slice(0, 800),
            title: (c.querySelector('h2,h3,h4,h5,a,.title') || {}).innerText?.trim() || '',
            href: (c.querySelector('a[href]') || {}).href || '',
            org: (c.querySelector('.org,.department,.buyer,.organisation') || {}).innerText?.trim() || '',
            date: (c.querySelector('.date,.deadline,.closing') || {}).innerText?.trim() || '',
            location: (c.querySelector('.location,.state,.city') || {}).innerText?.trim() || '',
            value: (c.querySelector('.value,.amount,.cost') || {}).innerText?.trim() || '',
        }))
    };
}"""


class TenderTigerAgent(_AggregatorBase):
    """
    Scrapes tendertiger.com -- a major national tender aggregation portal.
    Provides tenders across sectors with organisation, closing date, and value.
    """

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config, session)

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        save_callback=None,
    ) -> ScrapeResult:
        url = self.config.results_url or self.config.base_url
        return await self._paginated_scrape(
            url,
            max_pages=max_pages,
            org_filter=org_filter,
            fetch_details=fetch_details,
            save_callback=save_callback,
            progress_cb=progress_cb,
            parse_fn=self._parse_page,
        )

    async def _parse_page(self, page, page_num: int) -> list[dict]:
        """Extract tenders from tendertiger.com page."""
        data = await page.evaluate(TENDERTIGER_JS)

        if data.get("type") == "table":
            headers = data.get("headers", [])
            tenders = []
            for item in data.get("items", []):
                cells = item.get("cells", [])
                if not cells:
                    continue
                mapped = _heuristic_map(headers, cells)
                def c(i):
                    return cells[i].strip() if i < len(cells) else ""

                tender = _build_tender(
                    self.portal_id,
                    self.config.display_name,
                    ref_number=mapped.get("ref_number", "") or c(0),
                    title=mapped.get("title", "") or c(1),
                    organisation=mapped.get("organisation", ""),
                    published_date=mapped.get("published_date", ""),
                    closing_date=mapped.get("closing_date", ""),
                    tender_value_inr=mapped.get("tender_value_inr", ""),
                    location=mapped.get("location", ""),
                    tender_type=mapped.get("tender_type", ""),
                    detail_url=item.get("href", ""),
                    page_num=page_num,
                )
                tenders.append(tender)
            return tenders

        elif data.get("type") == "cards":
            return self._parse_cards(data.get("items", []), page_num)

        return await self._extract_tenders_auto(page, page_num)


# ---------------------------------------------------------------------------
# TenderKart.in
# ---------------------------------------------------------------------------

class TenderKartAgent(_AggregatorBase):
    """
    Scrapes tenderkart.in -- a tender aggregator with category browsing.
    Targets the latest tenders listing page.
    """

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config, session)

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        save_callback=None,
    ) -> ScrapeResult:
        url = self.config.results_url or self.config.base_url
        return await self._paginated_scrape(
            url,
            max_pages=max_pages,
            org_filter=org_filter,
            fetch_details=fetch_details,
            save_callback=save_callback,
            progress_cb=progress_cb,
        )


# ---------------------------------------------------------------------------
# Tender247.com
# ---------------------------------------------------------------------------

TENDER247_JS = """() => {
    // tender247 typically shows tenders in a table with pagination
    const table = document.querySelector('table.table, table[id*="tender"], table');
    if (!table) return { type: 'empty', items: [], headers: [] };

    const headerRow = table.querySelector('thead tr') || table.querySelector('tr:first-child');
    const headers = headerRow
        ? Array.from(headerRow.querySelectorAll('th, td')).map(c => c.innerText.trim().toLowerCase())
        : [];

    const dataRows = Array.from(table.querySelectorAll('tbody tr, tr'))
        .filter(r => r.querySelectorAll('td').length >= 2 && !r.querySelector('th'));

    return {
        type: dataRows.length ? 'table' : 'empty',
        headers,
        items: dataRows.slice(0, 300).map(row => {
            const cells = Array.from(row.querySelectorAll('td'));
            const link = row.querySelector('a[href]');
            return {
                cells: cells.map(c => c.innerText.trim().replace(/\\s+/g, ' ')),
                href: link ? link.href : ''
            };
        })
    };
}"""


class Tender247Agent(_AggregatorBase):
    """
    Scrapes tender247.com -- a commercial aggregator offering tabular listings
    of Indian government tenders with ref number, title, closing date, and value.
    """

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config, session)

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        save_callback=None,
    ) -> ScrapeResult:
        url = self.config.results_url or self.config.base_url
        return await self._paginated_scrape(
            url,
            max_pages=max_pages,
            org_filter=org_filter,
            fetch_details=fetch_details,
            save_callback=save_callback,
            progress_cb=progress_cb,
            parse_fn=self._parse_page,
        )

    async def _parse_page(self, page, page_num: int) -> list[dict]:
        """Extract tenders from tender247.com page."""
        data = await page.evaluate(TENDER247_JS)

        if data.get("type") != "table":
            return await self._extract_tenders_auto(page, page_num)

        headers = data.get("headers", [])
        tenders = []
        for item in data.get("items", []):
            cells = item.get("cells", [])
            if not cells:
                continue
            mapped = _heuristic_map(headers, cells)
            def c(i):
                return cells[i].strip() if i < len(cells) else ""

            tender = _build_tender(
                self.portal_id,
                self.config.display_name,
                ref_number=mapped.get("ref_number", "") or c(0),
                title=mapped.get("title", "") or c(1),
                organisation=mapped.get("organisation", ""),
                published_date=mapped.get("published_date", ""),
                closing_date=mapped.get("closing_date", ""),
                tender_value_inr=mapped.get("tender_value_inr", ""),
                location=mapped.get("location", ""),
                tender_type=mapped.get("tender_type", ""),
                detail_url=item.get("href", ""),
                page_num=page_num,
            )
            tenders.append(tender)
        return tenders


# ---------------------------------------------------------------------------
# GujaratTenders.in
# ---------------------------------------------------------------------------

class GujaratTendersAgent(_AggregatorBase):
    """
    Scrapes gujarattenders.in -- a Gujarat-focused tender aggregator.
    Lists state government and PSU tenders from Gujarat with location, value,
    and closing date information.
    """

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config, session)

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        save_callback=None,
    ) -> ScrapeResult:
        url = self.config.results_url or self.config.base_url
        return await self._paginated_scrape(
            url,
            max_pages=max_pages,
            org_filter=org_filter,
            fetch_details=fetch_details,
            save_callback=save_callback,
            progress_cb=progress_cb,
        )


# ---------------------------------------------------------------------------
# Palladium (SPA with API interception)
# ---------------------------------------------------------------------------

class PalladiumAgent(BaseAgent):
    """
    Scrapes app.palladium.primenumbers.in -- an Angular/React single-page
    application.  Instead of parsing DOM, we intercept XHR/fetch API responses
    to capture tender data directly from the JSON payloads.

    Strategy:
        1. Set up a route handler to intercept API responses containing tender data.
        2. Navigate to the app and wait for initial API calls to complete.
        3. Trigger pagination by interacting with the SPA or modifying API params.
        4. Collect intercepted JSON responses and map to our schema.
    """

    def __init__(self, config: PortalConfig, session: BrowserSession):
        super().__init__(config)
        self.session = session

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        save_callback=None,
    ) -> ScrapeResult:
        result = ScrapeResult(portal_id=self.portal_id)
        ctx = await self.session.new_context()
        page = await self.session.new_page(ctx)

        intercepted_data: list[dict] = []

        async def _on_response(response):
            """Intercept API responses that look like tender data."""
            url = response.url
            if response.status != 200:
                return
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                return
            # Capture responses from API endpoints that likely contain tender data
            api_keywords = ["tender", "bid", "procurement", "notice", "rfp", "rfq", "listing"]
            if not any(kw in url.lower() for kw in api_keywords):
                return
            try:
                body = await response.json()
                intercepted_data.append({
                    "url": url,
                    "data": body,
                })
                log.info(f"[palladium] Intercepted API: {url}")
            except Exception:
                pass

        try:
            page.on("response", _on_response)

            base_url = self.config.results_url or self.config.base_url
            log.info(f"[palladium] Loading SPA: {base_url}")
            await page.goto(base_url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(5000)

            SCREENSHOTS_DIR.mkdir(exist_ok=True)
            await page.screenshot(
                path=str(SCREENSHOTS_DIR / "palladium_debug.png"), full_page=True
            )

            # Wait for SPA to fully hydrate and make API calls
            await page.wait_for_timeout(3000)

            # Try clicking on common navigation elements to trigger data loads
            nav_selectors = [
                'a[href*="tender"]', 'a[href*="bid"]', 'a[href*="procurement"]',
                'button:has-text("Tender")', 'button:has-text("Search")',
                'a:has-text("Tenders")', 'a:has-text("Browse")',
                '[routerlink*="tender"]', '[routerlink*="bid"]',
            ]
            for sel in nav_selectors:
                try:
                    locator = page.locator(sel).first
                    if await locator.is_visible(timeout=1000):
                        await locator.click()
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                        await page.wait_for_timeout(2000)
                        break
                except Exception:
                    continue

            # Paginate through available pages
            current_page = 1
            pages_scraped = 0

            while True:
                if max_pages and pages_scraped >= max_pages:
                    break

                # Process any intercepted data from this page
                new_tenders = self._process_intercepted(intercepted_data)
                intercepted_data.clear()

                if new_tenders:
                    if org_filter:
                        new_tenders = [
                            t for t in new_tenders
                            if org_filter.lower() in t.get("organisation", "").lower()
                        ]
                    result.tenders.extend(new_tenders)
                    pages_scraped += 1
                    result.pages = pages_scraped

                    log.info(
                        f"[palladium] Page {pages_scraped}: "
                        f"{len(new_tenders)} tenders (total: {len(result.tenders)})"
                    )

                    if save_callback:
                        try:
                            save_callback(self.portal_id, result.tenders)
                        except Exception as e:
                            log.warning(f"[palladium] save_callback error: {e}")

                    if progress_cb:
                        await progress_cb(pages_scraped, len(result.tenders))

                # Try to go to next page in the SPA
                next_clicked = await self._click_next_spa(page)
                if not next_clicked:
                    # If no intercepted data was captured at all, try DOM fallback
                    if not result.tenders:
                        log.info("[palladium] No API data intercepted, trying DOM fallback")
                        dom_tenders = await self._dom_fallback(page, current_page)
                        if dom_tenders:
                            result.tenders.extend(dom_tenders)
                            result.pages = 1
                    break

                await page.wait_for_timeout(3000)
                current_page += 1

            if not result.tenders:
                result.skip_reason = "No tender data found via API interception or DOM"
                log.warning(f"[palladium] {result.skip_reason}")

        except Exception as e:
            log.error(f"[palladium] Error: {e}", exc_info=True)
            result.errors.append(str(e))
            try:
                await page.screenshot(
                    path=str(SCREENSHOTS_DIR / "palladium_error.png")
                )
            except Exception:
                pass
        finally:
            await ctx.close()

        log.info(
            f"[palladium] Done: {len(result.tenders)} tenders "
            f"across {result.pages} pages"
        )
        return result

    def _process_intercepted(self, intercepted: list[dict]) -> list[dict]:
        """Convert intercepted API JSON into standardised tender dicts."""
        tenders = []
        for entry in intercepted:
            data = entry.get("data")
            if not data:
                continue

            # Handle various response shapes: list, paginated object, nested
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                # Common pagination wrappers
                for key in ["data", "results", "tenders", "items", "content",
                            "records", "bids", "notices", "list"]:
                    val = data.get(key)
                    if isinstance(val, list) and val:
                        items = val
                        break
                if not items and len(data) > 3:
                    # Single tender object
                    items = [data]

            for item in items:
                if not isinstance(item, dict):
                    continue
                tender = self._map_api_item(item)
                if tender.get("title") or tender.get("ref_number"):
                    tenders.append(tender)

        return tenders

    def _map_api_item(self, item: dict) -> dict:
        """Map a single API response item to our schema."""
        # Flexible field extraction: try multiple possible key names
        def pick(*keys, default=""):
            for k in keys:
                val = item.get(k)
                if val is not None and str(val).strip():
                    return str(val).strip()
            return default

        return _build_tender(
            self.portal_id,
            self.config.display_name,
            ref_number=pick(
                "tenderNumber", "tender_number", "refNumber", "ref_number",
                "bidNumber", "id", "noticeId", "tender_id",
            ),
            title=pick(
                "title", "tenderTitle", "tender_title", "subject",
                "description", "name", "workDescription",
            ),
            organisation=pick(
                "organisation", "organization", "org", "deptName",
                "department", "buyer", "authority", "agency",
            ),
            published_date=pick(
                "publishedDate", "published_date", "publishDate",
                "created", "createdAt", "postDate", "issueDate",
            ),
            closing_date=pick(
                "closingDate", "closing_date", "closeDate",
                "deadline", "dueDate", "submissionEnd", "lastDate",
            ),
            opening_date=pick(
                "openingDate", "opening_date", "bidOpenDate",
            ),
            tender_value_inr=pick(
                "tenderValue", "tender_value", "estimatedValue", "value",
                "amount", "cost", "ecv", "budget",
            ),
            location=pick(
                "location", "state", "city", "district", "region", "place",
            ),
            tender_type=pick(
                "tenderType", "tender_type", "type", "category", "procurementType",
            ),
            status=pick("status", "statusText", default="Active"),
            detail_url=pick("detailUrl", "detail_url", "url", "link"),
        )

    async def _click_next_spa(self, page) -> bool:
        """Try to click a next-page control in the SPA."""
        selectors = [
            'button[aria-label="Next"]',
            'button:has-text("Next")',
            'a:has-text("Next")',
            'mat-paginator button.mat-paginator-navigation-next',
            '.pagination .next a',
            'li.next a',
            'button.next-page',
            '[class*="next"]',
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible(timeout=1000):
                    disabled = await locator.is_disabled()
                    if not disabled:
                        await locator.click()
                        return True
            except Exception:
                continue
        return False

    async def _dom_fallback(self, page, page_num: int) -> list[dict]:
        """
        Fallback: extract whatever text/tables are visible in the DOM
        when API interception yields nothing.
        """
        table_data = await page.evaluate(AUTO_TABLE_JS)
        rows = table_data.get("rows", [])
        headers = table_data.get("headers", [])

        if rows:
            tenders = []
            for row in rows:
                cells = row.get("cells", [])
                if not cells:
                    continue
                mapped = _heuristic_map(headers, cells)
                def c(i):
                    return cells[i].strip() if i < len(cells) else ""
                tender = _build_tender(
                    self.portal_id,
                    self.config.display_name,
                    ref_number=mapped.get("ref_number", "") or c(0),
                    title=mapped.get("title", "") or c(1),
                    organisation=mapped.get("organisation", ""),
                    closing_date=mapped.get("closing_date", ""),
                    detail_url=row.get("detail_href", ""),
                    page_num=page_num,
                )
                tenders.append(tender)
            return tenders

        return []


# ---------------------------------------------------------------------------
# Convenience: map portal_id -> Agent class
# ---------------------------------------------------------------------------

AGGREGATOR_AGENTS: dict[str, type] = {
    "tenderdetail": TenderDetailAgent,
    "tendertiger": TenderTigerAgent,
    "tenderkart": TenderKartAgent,
    "tender247": Tender247Agent,
    "gujarattenders": GujaratTendersAgent,
    "palladium": PalladiumAgent,
}


def get_aggregator_agent(
    portal_id: str, session: BrowserSession
) -> BaseAgent | None:
    """
    Factory: return an instantiated aggregator agent for the given portal_id,
    or None if the portal_id is not a known aggregator.

    Usage:
        agent = get_aggregator_agent("tenderdetail", browser_session)
        if agent:
            result = await agent.scrape(max_pages=5)
    """
    cls = AGGREGATOR_AGENTS.get(portal_id)
    cfg = AGGREGATOR_CONFIGS.get(portal_id)
    if cls and cfg:
        return cls(cfg, session)
    return None
