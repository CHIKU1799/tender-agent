"""
Universal Tender Scraper — works on ANY website.
Uses AI (GPT-4o) to extract tender data from any page layout.
Fallback for portals without a dedicated agent.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
import random
from datetime import datetime
from typing import Optional

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay, wait_for_content, human_scroll
from ai.captcha_advanced import solve_any_captcha

log = logging.getLogger("agents.universal")


class UniversalAgent(BaseAgent):
    """
    Scrapes any tender website using:
    1. Structured table extraction (fast, no AI)
    2. Pattern-based text extraction
    3. GPT-4o HTML parsing (if OPENAI_API_KEY set)
    """

    def __init__(self, cfg, session: BrowserSession, scope: str = "active"):
        super().__init__(cfg)
        self.session = session
        self.scope = scope

    async def scrape(
        self,
        max_pages: Optional[int] = None,
        org_filter: Optional[str] = None,
        fetch_details: bool = False,
        progress_cb=None,
    ) -> ScrapeResult:

        result   = ScrapeResult(portal_id=self.config.portal_id)
        tenders  = []
        max_p    = max_pages or 5

        try:
            ctx  = await self.session.new_context(portal_id=self.config.portal_id)
            page = await self.session.new_page(ctx, portal_id=self.config.portal_id)

            # Determine start URL based on scope
            if self.scope in ("archive", "both") and self.config.archive_url:
                start_url = self.config.archive_url
            elif self.scope in ("awards",) and self.config.awards_url:
                start_url = self.config.awards_url
            elif self.config.search_url:
                start_url = self.config.search_url
            else:
                start_url = self.config.base_url

            status = {
                "archive": "Archive", "awards": "Awarded"
            }.get(self.scope, "Active")

            page_num = 0
            current_url = start_url

            while page_num < max_p:
                page_num += 1
                log.info(f"[universal] {self.config.portal_id} page {page_num}: {current_url}")

                await page.goto(current_url, wait_until="domcontentloaded", timeout=45_000)
                await wait_for_content(page)
                await solve_any_captcha(page)
                await human_scroll(page, 2)

                # Extract tenders from this page
                rows = await self._extract_tenders(page, status)

                if not rows:
                    # Try AI extraction as last resort
                    rows = await self._ai_extract(page, status)

                tenders.extend(rows)
                log.info(f"[universal] {self.config.portal_id} page {page_num}: {len(rows)} rows")

                if progress_cb:
                    await progress_cb(page_num, len(tenders))

                # Find next page
                next_url = await self._find_next_page(page, current_url, page_num)
                if not next_url or next_url == current_url:
                    break

                current_url = next_url
                await random_delay(2.0, 5.0)

            # Fetch details if requested
            if fetch_details and tenders:
                tenders = await self._enrich_details(page, tenders)

            await self.session.save_cookies(ctx, self.config.portal_id)
            await ctx.close()

        except Exception as e:
            log.error(f"[universal] {self.config.portal_id}: {e}")
            result.errors.append(str(e))

        result.tenders = tenders
        result.pages   = max(1, len(tenders) // 20)
        return result

    async def _extract_tenders(self, page, status: str) -> list[dict]:
        """Extract tenders using table + pattern matching — no AI needed."""
        rows = []

        # ── Try table extraction ──────────────────────────────────────────────
        table_sels = [
            "table.tender-table", "table#tender-list", "table.list_table",
            "table.tablesorter", "table.table-striped", "table.data-table",
            ".tender-results table", "#results table", "table",
        ]

        for sel in table_sels:
            try:
                table = page.locator(sel).first
                if await table.count() == 0:
                    continue

                tbody_rows = table.locator("tbody tr")
                count = await tbody_rows.count()
                if count < 1:
                    continue

                # Get headers
                headers = []
                try:
                    header_els = table.locator("thead th, thead td")
                    for i in range(await header_els.count()):
                        h = (await header_els.nth(i).inner_text()).strip().lower()
                        headers.append(h)
                except Exception:
                    pass

                for i in range(count):
                    row = tbody_rows.nth(i)
                    cells = []
                    cell_els = row.locator("td")
                    for j in range(await cell_els.count()):
                        cells.append((await cell_els.nth(j).inner_text()).strip())

                    if len(cells) < 2:
                        continue

                    detail_url = ""
                    try:
                        link = row.locator("a").first
                        if await link.count() > 0:
                            href = await link.get_attribute("href") or ""
                            if href and "javascript" not in href:
                                base = self.config.base_url.rstrip("/")
                                detail_url = href if href.startswith("http") else base + "/" + href.lstrip("/")
                    except Exception:
                        pass

                    t = self._cells_to_tender(cells, headers, detail_url, status)
                    if t:
                        rows.append(t)

                if rows:
                    log.debug(f"[universal] table {sel}: {len(rows)} rows")
                    break

            except Exception as e:
                log.debug(f"[universal] table sel {sel}: {e}")
                continue

        # ── Try card/list extraction ──────────────────────────────────────────
        if not rows:
            card_sels = [
                ".tender-card", ".tender-item", ".bid-item",
                ".procurement-item", ".result-item", ".list-item",
                "[class*='tender']", "[class*='bid']",
            ]
            for sel in card_sels:
                try:
                    cards = page.locator(sel)
                    count = await cards.count()
                    if count < 1:
                        continue

                    for i in range(count):
                        card = cards.nth(i)
                        text = await card.inner_text()
                        detail_url = ""
                        try:
                            link = card.locator("a").first
                            if await link.count() > 0:
                                href = await link.get_attribute("href") or ""
                                if href and "javascript" not in href:
                                    base = self.config.base_url.rstrip("/")
                                    detail_url = href if href.startswith("http") else base + "/" + href.lstrip("/")
                        except Exception:
                            pass

                        t = self._text_to_tender(text, detail_url, status)
                        if t:
                            rows.append(t)

                    if rows:
                        break
                except Exception:
                    continue

        return rows

    def _cells_to_tender(self, cells: list[str], headers: list[str], detail_url: str, status: str) -> Optional[dict]:
        """Map table cells to tender fields using header matching."""
        if not any(cells):
            return None

        t = {
            "portal_id":   self.config.portal_id,
            "portal_name": self.config.display_name,
            "state":       self.config.state if hasattr(self.config, "state") else "",
            "source_website": self.config.base_url,
            "detail_url":  detail_url,
            "status":      status,
            "scraped_at":  datetime.utcnow().isoformat(),
        }

        def s(i): return cells[i] if i < len(cells) else ""

        if headers:
            for i, h in enumerate(headers):
                val = s(i)
                if not val:
                    continue
                if any(k in h for k in ("title", "subject", "work", "description")):
                    t["title"] = val
                elif any(k in h for k in ("org", "dept", "department", "ministry", "authority")):
                    t["organisation"] = val
                elif any(k in h for k in ("tender id", "tender no", "nit", "ref")):
                    t["tender_id"] = val
                    t["ref_number"] = val
                elif any(k in h for k in ("publish", "start", "issue")):
                    t["published_date"] = val
                elif any(k in h for k in ("clos", "end", "due", "last", "deadline")):
                    t["closing_date"] = val
                elif any(k in h for k in ("value", "amount", "cost", "₹")):
                    t["tender_value_inr"] = val
                elif any(k in h for k in ("state", "location", "place")):
                    t["state"] = val
                elif any(k in h for k in ("type", "category")):
                    t["tender_type"] = val
                elif any(k in h for k in ("award", "winner", "vendor")):
                    t["award_winner"] = val
        else:
            # No headers — positional guess
            t["tender_id"]      = s(0) or s(1)
            t["title"]          = s(2) if len(cells) > 2 else s(1)
            t["organisation"]   = s(3) if len(cells) > 3 else ""
            t["published_date"] = s(4) if len(cells) > 4 else ""
            t["closing_date"]   = s(5) if len(cells) > 5 else ""
            t["tender_value_inr"] = s(6) if len(cells) > 6 else ""

        title = t.get("title", "")
        if not title or title.lower() in ("s.no", "sr.no", "sno", "title", "#"):
            return None

        return t

    def _text_to_tender(self, text: str, detail_url: str, status: str) -> Optional[dict]:
        """Extract tender fields from free-form text using regex patterns."""
        if not text or len(text) < 20:
            return None

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = lines[0] if lines else ""

        if not title or len(title) < 5:
            return None

        t = {
            "portal_id":      self.config.portal_id,
            "portal_name":    self.config.display_name,
            "state":          getattr(self.config, "state", ""),
            "source_website": self.config.base_url,
            "title":          title,
            "detail_url":     detail_url,
            "status":         status,
            "scraped_at":     datetime.utcnow().isoformat(),
        }

        patterns = {
            "tender_id":       r"(?:Tender No|NIT No|Bid No|Ref)[.:\s]+([A-Z0-9/\-]+)",
            "organisation":    r"(?:Org|Dept|Ministry|Authority)[.:\s]+(.+?)(?:\n|$)",
            "published_date":  r"(?:Published|Start|Issue)[.:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            "closing_date":    r"(?:Clos|End|Due|Last|Deadline)[.:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            "tender_value_inr": r"(?:Value|Amount|₹|Rs\.?)[.:\s]+([\d,\.]+\s*(?:Cr|L|Lakh|crore)?)",
            "emd_inr":         r"(?:EMD|Earnest)[.:\s]+([\d,\.]+)",
            "location":        r"(?:Location|Place|State)[.:\s]+(.+?)(?:\n|$)",
        }

        for field, pattern in patterns.items():
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                t[field] = m.group(1).strip()

        return t

    async def _ai_extract(self, page, status: str) -> list[dict]:
        """Use GPT-4o to extract tenders when pattern matching fails."""
        try:
            import os
            if not os.getenv("OPENAI_API_KEY"):
                return []

            from ai.client import get_client
            client = get_client()

            # Get page text (truncated)
            content = await page.inner_text("body")
            content = content[:6000]  # Limit tokens

            resp = await client.chat.completions.create(
                model="gpt-4o",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"""Extract all tender listings from this Indian government portal page.
Return a JSON array of objects with these fields:
tender_id, title, organisation, published_date, closing_date, tender_value_inr, location, status

Page content:
{content}

Return ONLY valid JSON array, no other text."""
                }]
            )

            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            items = json.loads(raw)

            tenders = []
            for item in items:
                if isinstance(item, dict) and item.get("title"):
                    item.update({
                        "portal_id":      self.config.portal_id,
                        "portal_name":    self.config.display_name,
                        "state":          getattr(self.config, "state", ""),
                        "source_website": self.config.base_url,
                        "status":         status,
                        "scraped_at":     datetime.utcnow().isoformat(),
                    })
                    tenders.append(item)

            log.info(f"[universal/ai] {self.config.portal_id}: extracted {len(tenders)} tenders via GPT-4o")
            return tenders

        except Exception as e:
            log.debug(f"[universal/ai] {e}")
            return []

    async def _find_next_page(self, page, current_url: str, page_num: int) -> Optional[str]:
        """Find the next page URL."""
        next_sels = [
            "a:has-text('Next')", "a:has-text('>')", "a:has-text('»')",
            ".next a", "li.next a", "a.next-page",
            f"a:has-text('{page_num + 1}')",
            "[aria-label='Next']", "[title='Next Page']",
        ]
        for sel in next_sels:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    cls = await el.get_attribute("class") or ""
                    if "disabled" in cls.lower():
                        return None
                    href = await el.get_attribute("href") or ""
                    if href and "javascript" not in href:
                        base = self.config.base_url.rstrip("/")
                        return href if href.startswith("http") else base + "/" + href.lstrip("/")
                    # Click-based pagination
                    await el.click()
                    await wait_for_content(page, timeout=20_000)
                    return page.url
            except Exception:
                continue
        return None

    async def _enrich_details(self, page, tenders: list[dict]) -> list[dict]:
        """Visit detail pages to get complete tender info."""
        enriched = []
        for i, t in enumerate(tenders[:50]):  # Limit to 50 to avoid timeout
            if not t.get("detail_url"):
                enriched.append(t)
                continue
            try:
                await page.goto(t["detail_url"], wait_until="domcontentloaded", timeout=30_000)
                await wait_for_content(page)

                text = await page.inner_text("body")
                fields = {
                    "emd_inr":           r"(?:EMD|Earnest)[.:\s]+([\d,\.]+)",
                    "tender_fee_inr":    r"(?:Tender Fee|Doc Fee)[.:\s]+([\d,\.]+)",
                    "bid_submission_end": r"(?:Bid Submit|Last Date.*?Bid)[.:\s]+([^\n]+)",
                    "pre_bid_meeting":   r"(?:Pre[- ]Bid)[.:\s]+([^\n]+)",
                    "work_description":  r"(?:Scope|Description|Work)[.:\s]+([^\n]{30,300})",
                    "contact":           r"(?:Contact|Phone|Email)[.:\s]+([^\n]+)",
                    "pincode":           r"\b(\d{6})\b",
                    "tender_type":       r"(?:Type)[.:\s]+([^\n]+)",
                    "tender_category":   r"(?:Category)[.:\s]+([^\n]+)",
                    "award_winner":      r"(?:Awarded To|Winner|Contractor)[.:\s]+([^\n]+)",
                    "award_date":        r"(?:Award Date)[.:\s]+([^\n]+)",
                    "award_amount":      r"(?:Award Amount|Contract Value)[.:\s]+([\d,\.]+)",
                }
                for field, pattern in fields.items():
                    if not t.get(field):
                        m = re.search(pattern, text, re.IGNORECASE)
                        if m:
                            t[field] = m.group(1).strip()

                t["detail_scraped"] = "true"
                log.debug(f"[universal] detail {i+1}/{len(tenders)}: OK")
                await random_delay(1.0, 2.5)

            except Exception as e:
                log.debug(f"[universal] detail {i}: {e}")

            enriched.append(t)
        return enriched
