"""
Karnataka e-Procurement Scraper (eproc.karnataka.gov.in)
=========================================================
Targets: https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam

This is the REAL Karnataka government e-procurement portal (not KPPP).
Uses a JSF/Seam session-based POST pagination pattern.

Strategies:
  1. Primary: requests.Session with JSESSIONID + POST pagination (fast, no browser)
  2. Fallback: Playwright stealth mode if CAPTCHA detected (uses ai.captcha_advanced)

Supports: active + archive (closed/awarded) scraping with pricing/awardee data.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import warnings
from datetime import datetime, timedelta
from typing import Optional

import requests
import urllib3
from bs4 import BeautifulSoup

from agents.base import BaseAgent, ScrapeResult
from core.browser import BrowserSession, random_delay
from core.utils import now_iso

# Suppress SSL warnings — Karnataka portal has cert issues
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

log = logging.getLogger("agents.karnataka_eproc")

BASE_URL = "https://eproc.karnataka.gov.in"
TENDERS_URL = f"{BASE_URL}/eprocurement/common/eproc_tenders_list.seam"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": TENDERS_URL,
    "Origin": BASE_URL,
    "Content-Type": "application/x-www-form-urlencoded",
}


class KarnatakaEprocAgent(BaseAgent):
    """
    Scrapes Karnataka e-Procurement for active + archive tenders.
    Uses requests.Session for speed; falls back to Playwright on CAPTCHA.
    """

    def __init__(self, cfg, session: BrowserSession, scope: str = "active"):
        super().__init__(cfg)
        self.browser_session = session
        self.scope = scope
        self._http: Optional[requests.Session] = None
        self._jsessionid: str = ""

    # ── Public API ───────────────────────────────────────────────────────────

    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        fetch_details: bool = False,
        progress_cb=None,
        days_back: int = 365,
    ) -> ScrapeResult:

        result = ScrapeResult(portal_id=self.portal_id)
        scopes = (
            ["active", "archive"] if self.scope in ("both", "all")
            else [self.scope]
        )

        for sc in scopes:
            try:
                batch = await self._scrape_scope(
                    sc, max_pages or 20, org_filter, fetch_details, progress_cb, days_back
                )
                result.tenders.extend(batch)
                log.info(f"[karnataka_eproc] {sc}: {len(batch)} tenders")
            except Exception as e:
                log.error(f"[karnataka_eproc] {sc} error: {e}")
                result.errors.append(f"{sc}: {e}")

        return result

    # ── Scope dispatcher ─────────────────────────────────────────────────────

    async def _scrape_scope(self, scope, max_pages, org_filter, fetch_details, progress_cb, days_back):
        """Scrape one scope (active or archive)."""
        # Initialize HTTP session
        self._init_session()
        ok = await self._get_jsessionid()
        if not ok:
            log.warning("[karnataka_eproc] Failed to get JSESSIONID, trying Playwright fallback")
            return await self._playwright_fallback(scope, max_pages, org_filter, progress_cb, days_back)

        # Check for CAPTCHA on initial page
        if self._has_captcha:
            log.info("[karnataka_eproc] CAPTCHA detected, switching to Playwright")
            return await self._playwright_fallback(scope, max_pages, org_filter, progress_cb, days_back)

        all_tenders = []
        prev_ids = set()

        for page_num in range(1, max_pages + 1):
            try:
                html = await self._fetch_page(page_num, scope, days_back)
                if not html:
                    break

                tenders = self._parse_html(html, scope)
                if not tenders:
                    log.info(f"[karnataka_eproc] Page {page_num}: no rows found")
                    break

                # Dedup check — stop if same data as previous page
                current_ids = {t.get("tender_id", "") for t in tenders}
                if current_ids == prev_ids:
                    log.info(f"[karnataka_eproc] Page {page_num}: duplicate data, stopping")
                    break
                prev_ids = current_ids

                # Apply org filter
                if org_filter:
                    tenders = [t for t in tenders if org_filter.lower() in t.get("organisation", "").lower()]

                all_tenders.extend(tenders)
                log.info(f"[karnataka_eproc] Page {page_num}: {len(tenders)} rows ({len(all_tenders)} total)")

                if progress_cb:
                    await progress_cb(page_num, len(all_tenders))

                # Polite delay
                time.sleep(random.uniform(1.5, 3.5))

            except Exception as e:
                log.warning(f"[karnataka_eproc] Page {page_num} error: {e}")
                break

        # Fetch detail pages for awardee/pricing info
        if fetch_details and all_tenders:
            all_tenders = await self._enrich_details(all_tenders)

        return all_tenders

    # ── HTTP Session Management ──────────────────────────────────────────────

    def _init_session(self):
        """Create a new requests.Session with stealth headers."""
        self._http = requests.Session()
        self._http.headers.update(HEADERS)
        self._http.verify = False
        self._has_captcha = False

    async def _get_jsessionid(self) -> bool:
        """GET the tender list page to establish JSESSIONID cookie."""
        try:
            resp = self._http.get(TENDERS_URL, timeout=30)
            resp.raise_for_status()

            # Extract JSESSIONID from cookies
            for cookie in self._http.cookies:
                if cookie.name.upper() == "JSESSIONID":
                    self._jsessionid = cookie.value
                    log.info(f"[karnataka_eproc] Got JSESSIONID: {self._jsessionid[:20]}...")
                    break

            if not self._jsessionid:
                # Try from Set-Cookie header
                sc = resp.headers.get("Set-Cookie", "")
                m = re.search(r"JSESSIONID=([^;]+)", sc)
                if m:
                    self._jsessionid = m.group(1)

            # Check for CAPTCHA
            html_lower = resp.text.lower()
            if "captcha" in html_lower or "kaptcha" in html_lower:
                self._has_captcha = True
                log.info("[karnataka_eproc] CAPTCHA detected on initial page")

            # Extract javax.faces.ViewState for JSF form submissions
            soup = BeautifulSoup(resp.text, "html.parser")
            vs_input = soup.find("input", {"name": "javax.faces.ViewState"})
            self._view_state = vs_input["value"] if vs_input else ""

            return bool(self._jsessionid)

        except Exception as e:
            log.error(f"[karnataka_eproc] Session init failed: {e}")
            return False

    async def _fetch_page(self, page_num: int, scope: str, days_back: int) -> str:
        """POST to fetch a specific page of results."""
        url = TENDERS_URL
        if self._jsessionid:
            url = f"{TENDERS_URL};jsessionid={self._jsessionid}"

        # Build POST payload
        today = datetime.now()
        from_dt = today - timedelta(days=days_back)
        fmt = "%d/%m/%Y"

        payload = {
            "eprocTenders": "eprocTenders",
            "eprocTenders:tenderCreateDateFrom": from_dt.strftime(fmt),
            "eprocTenders:tenderCreateDateTo": today.strftime(fmt),
            "javax.faces.ViewState": self._view_state,
        }

        # Pagination — JSF data scroller
        if page_num > 1:
            payload["eprocTenders:_link_hidden_"] = f"eprocTenders:dataScrollerIdidx{page_num}"
            payload["eprocTenders:dataScrollerId"] = f"idx{page_num}"
        else:
            # First page: just submit the search
            payload["eprocTenders:butSearch"] = "Search"

        # Archive mode: filter by closed/awarded status
        if scope == "archive":
            payload["eprocTenders:tenderStatus"] = "Closed"
        elif scope == "awards":
            payload["eprocTenders:tenderStatus"] = "Awarded"

        try:
            resp = self._http.post(url, data=payload, timeout=45)
            resp.raise_for_status()

            # Update ViewState from response (JSF changes it each request)
            soup = BeautifulSoup(resp.text, "html.parser")
            vs_input = soup.find("input", {"name": "javax.faces.ViewState"})
            if vs_input:
                self._view_state = vs_input["value"]

            return resp.text

        except Exception as e:
            log.error(f"[karnataka_eproc] Page {page_num} fetch failed: {e}")
            return ""

    # ── HTML Parsing ─────────────────────────────────────────────────────────

    def _parse_html(self, html: str, scope: str) -> list[dict]:
        """Parse tender table rows from the HTML response."""
        soup = BeautifulSoup(html, "html.parser")
        tenders = []

        # Find the main results table — look for table with most data rows
        tables = soup.find_all("table")
        best_table = None
        max_rows = 0

        for table in tables:
            rows = table.find_all("tr")
            data_rows = [r for r in rows if r.find_all("td") and len(r.find_all("td")) >= 5]
            if len(data_rows) > max_rows:
                max_rows = len(data_rows)
                best_table = table

        if not best_table or max_rows == 0:
            return []

        # Get header mapping
        header_row = best_table.find("tr")
        headers = []
        if header_row:
            for th in header_row.find_all(["th", "td"]):
                headers.append(th.get_text(strip=True).lower())

        # Parse data rows
        for row in best_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]

            # Skip header-like rows
            if any(h in cell_texts[0].lower() for h in ["sl", "s.no", "serial", "department"]):
                continue

            # Extract detail link
            notice_url = ""
            for a_tag in row.find_all("a", href=True):
                href = a_tag["href"]
                if "tender" in href.lower() or "view" in href.lower() or "notice" in href.lower():
                    notice_url = href if href.startswith("http") else BASE_URL + href
                    break
            if not notice_url:
                # Any link
                first_link = row.find("a", href=True)
                if first_link:
                    href = first_link["href"]
                    notice_url = href if href.startswith("http") else BASE_URL + href

            # Map columns using headers or positional heuristics
            tender = self._map_columns(cell_texts, headers, notice_url, scope)
            if tender.get("title") or tender.get("tender_id"):
                tenders.append(tender)

        return tenders

    def _map_columns(self, cells: list[str], headers: list[str], notice_url: str, scope: str) -> dict:
        """Map table cells to tender fields using header heuristics."""
        def safe(i): return cells[i].strip() if i < len(cells) else ""

        # Try header-based mapping first
        mapped = {}
        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            hl = h.lower()
            val = cells[i].strip()

            if any(k in hl for k in ["department", "location", "office"]):
                mapped["department_location"] = val
            elif any(k in hl for k in ["tender no", "tender number", "reference", "ref"]):
                mapped["tender_number"] = val
            elif any(k in hl for k in ["title", "subject", "name", "work"]):
                mapped["tender_title"] = val
            elif any(k in hl for k in ["type"]):
                mapped["tender_type"] = val
            elif any(k in hl for k in ["category"]):
                mapped["category"] = val
            elif any(k in hl for k in ["estimated", "value", "amount", "cost"]):
                mapped["estimated_value"] = val
            elif any(k in hl for k in ["published", "created", "start"]):
                mapped["published_date"] = val
            elif any(k in hl for k in ["closing", "end", "due", "last", "bid end"]):
                mapped["bid_end_date"] = val
            elif any(k in hl for k in ["award", "winner", "contractor"]):
                mapped["awardee_name"] = val
            elif any(k in hl for k in ["contract", "awarded value"]):
                mapped["awarded_value"] = val

        # Fallback: positional mapping (Karnataka standard layout)
        if not mapped.get("tender_title"):
            mapped.setdefault("department_location", safe(0))
            mapped.setdefault("tender_number", safe(1))
            mapped.setdefault("tender_title", safe(2))
            mapped.setdefault("tender_type", safe(3))
            mapped.setdefault("category", safe(4))
            mapped.setdefault("estimated_value", safe(5))
            mapped.setdefault("published_date", safe(6))
            mapped.setdefault("bid_end_date", safe(7))

        # Derive status
        status = "Active"
        if scope == "archive":
            status = "Closed"
        elif scope == "awards":
            status = "Awarded"

        return {
            "portal_id":        self.portal_id,
            "portal_name":      self.config.display_name,
            "source_website":   BASE_URL,
            "tender_id":        mapped.get("tender_number", "") or safe(1),
            "ref_number":       mapped.get("tender_number", "") or safe(1),
            "title":            mapped.get("tender_title", "") or safe(2),
            "organisation":     mapped.get("department_location", "") or safe(0),
            "state":            "Karnataka",
            "published_date":   mapped.get("published_date", ""),
            "closing_date":     mapped.get("bid_end_date", ""),
            "opening_date":     "",
            "status":           status,
            "detail_url":       notice_url,
            "scraped_at":       now_iso(),
            "detail_scraped":   False,
            "tender_value_inr": mapped.get("estimated_value", ""),
            "tender_fee_inr":   "",
            "emd_inr":          "",
            "tender_type":      mapped.get("tender_type", ""),
            "tender_category":  mapped.get("category", ""),
            "location":         mapped.get("department_location", ""),
            "award_winner":     mapped.get("awardee_name", ""),
            "award_date":       "",
            "award_amount":     mapped.get("awarded_value", ""),
        }

    # ── Detail page enrichment ───────────────────────────────────────────────

    async def _enrich_details(self, tenders: list[dict]) -> list[dict]:
        """Visit detail pages to extract awardee, pricing, and status."""
        for i, t in enumerate(tenders[:50]):  # Limit to 50 detail fetches
            url = t.get("detail_url", "")
            if not url:
                continue

            try:
                resp = self._http.get(url, timeout=25)
                if resp.status_code != 200:
                    continue

                text = resp.text
                body = BeautifulSoup(text, "html.parser").get_text(separator="\n")

                # Extract awardee
                for pattern in [
                    r"(?:Award(?:ed)?\s*(?:to)?|L1\s*Bidder|Successful\s*Bidder|Contractor)[:\s]+([^\n]+)",
                    r"(?:Vendor|Supplier|Company)\s*(?:Name)?[:\s]+([^\n]+)",
                ]:
                    m = re.search(pattern, body, re.IGNORECASE)
                    if m and not t.get("award_winner"):
                        t["award_winner"] = m.group(1).strip()[:200]
                        break

                # Extract award amount
                for pattern in [
                    r"(?:Award(?:ed)?\s*(?:Amount|Value)|Contract\s*(?:Amount|Value))[:\s]*(?:Rs\.?\s*)?([₹\d,\.]+\s*(?:Cr|L|Lakh|crore)?)",
                    r"(?:L1\s*Amount|Bid\s*Amount)[:\s]*(?:Rs\.?\s*)?([₹\d,\.]+)",
                ]:
                    m = re.search(pattern, body, re.IGNORECASE)
                    if m and not t.get("award_amount"):
                        t["award_amount"] = m.group(1).strip()
                        break

                # Extract EMD
                m = re.search(r"(?:EMD|Earnest\s*Money)[:\s]*(?:Rs\.?\s*)?([₹\d,\.]+)", body, re.IGNORECASE)
                if m and not t.get("emd_inr"):
                    t["emd_inr"] = m.group(1).strip()

                # Extract tender fee
                m = re.search(r"(?:Tender\s*Fee|Document\s*Fee)[:\s]*(?:Rs\.?\s*)?([₹\d,\.]+)", body, re.IGNORECASE)
                if m and not t.get("tender_fee_inr"):
                    t["tender_fee_inr"] = m.group(1).strip()

                # Derive status from page content
                body_lower = body.lower()
                if "awarded" in body_lower and t.get("award_winner"):
                    t["status"] = "Awarded"
                elif "closed" in body_lower or "expired" in body_lower:
                    t["status"] = "Closed"

                t["detail_scraped"] = True
                log.debug(f"[karnataka_eproc] Detail {i+1}: {t['title'][:40]}")

                time.sleep(random.uniform(1.0, 2.5))

            except Exception as e:
                log.debug(f"[karnataka_eproc] Detail fetch error: {e}")

        return tenders

    # ── Playwright Fallback (CAPTCHA) ────────────────────────────────────────

    async def _playwright_fallback(self, scope, max_pages, org_filter, progress_cb, days_back):
        """Use Playwright + AI CAPTCHA solver when requests fails."""
        from ai.captcha_advanced import solve_any_captcha

        tenders = []
        ctx = await self.browser_session.new_context(portal_id="karnataka_eproc")
        page = await self.browser_session.new_page(ctx, portal_id="karnataka_eproc")

        try:
            log.info("[karnataka_eproc] Playwright fallback: loading tender list...")
            await page.goto(TENDERS_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # Solve CAPTCHA if present
            await solve_any_captcha(page, max_total_attempts=3)

            # Fill date range
            today = datetime.now()
            from_dt = today - timedelta(days=days_back)
            fmt = "%d/%m/%Y"

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

            # Set status filter for archive/awards
            if scope in ("archive", "awards"):
                status_val = "Awarded" if scope == "awards" else "Closed"
                await page.evaluate(f"""() => {{
                    const selects = Array.from(document.querySelectorAll("select"));
                    for (const sel of selects) {{
                        const opts = Array.from(sel.options);
                        for (const opt of opts) {{
                            if (opt.text.toLowerCase().includes("{status_val.lower()}")) {{
                                sel.value = opt.value;
                                sel.dispatchEvent(new Event('change', {{bubbles:true}}));
                                return true;
                            }}
                        }}
                    }}
                    return false;
                }}""")
                await page.wait_for_timeout(800)

            # Submit search
            try:
                await page.click("input[name='eprocTenders:butSearch']")
                await page.wait_for_load_state("networkidle", timeout=30_000)
                await page.wait_for_timeout(3000)
            except Exception as e:
                log.warning(f"[karnataka_eproc] Submit failed: {e}")
                await ctx.close()
                return tenders

            # Paginate
            for current_page in range(1, (max_pages or 20) + 1):
                html = await page.content()
                batch = self._parse_html(html, scope)

                if not batch:
                    break

                if org_filter:
                    batch = [t for t in batch if org_filter.lower() in t.get("organisation", "").lower()]

                tenders.extend(batch)
                log.info(f"[karnataka_eproc] PW page {current_page}: {len(batch)} rows")

                if progress_cb:
                    await progress_cb(current_page, len(tenders))

                # Try next page
                has_next = await page.evaluate("""() => {
                    const next = Array.from(document.querySelectorAll("a,input")).find(e => {
                        const t = (e.innerText||e.value||"").trim().toLowerCase();
                        return t === "next" || t === ">" || t === ">>";
                    });
                    return !!next;
                }""")

                if not has_next:
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
                    await page.wait_for_timeout(1500)
                except Exception:
                    break

                await random_delay(1.5, 3.0)

        except Exception as e:
            log.error(f"[karnataka_eproc] Playwright error: {e}")
        finally:
            await ctx.close()

        return tenders
