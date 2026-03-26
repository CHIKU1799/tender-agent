"""
Centralised Playwright browser session manager.
All agents receive a BrowserSession instead of managing their own playwright context.
"""
from __future__ import annotations
import asyncio
import random
from fake_useragent import UserAgent
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from playwright_stealth import Stealth


class BrowserSession:
    """
    Manages one Chromium browser instance shared across all agents in a run.
    GePNIC portals each get their own page (domain-scoped cookies don't clash).
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ua = UserAgent()

    async def start(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

    async def new_context(self) -> BrowserContext:
        """Create a fresh browser context (isolated cookies/session per portal)."""
        ctx = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=self._ua.chrome,
            locale="en-US",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        return ctx

    async def new_page(self, context: BrowserContext | None = None) -> Page:
        """Create a stealth-patched page in the given context (or a new one)."""
        ctx = context or await self.new_context()
        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)
        return page

    async def rotate_ua(self, context: BrowserContext):
        await context.set_extra_http_headers({"User-Agent": self._ua.chrome})

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()


async def random_delay(min_s: float = 2.5, max_s: float = 5.0):
    await asyncio.sleep(random.uniform(min_s, max_s))
