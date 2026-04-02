"""
Centralised Playwright browser session manager.
Enhanced with full anti-detection, fingerprint spoofing,
cookie persistence, and residential proxy support.
"""
from __future__ import annotations
import asyncio
import json
import logging
import random
import os
from pathlib import Path
from fake_useragent import UserAgent
from playwright.async_api import (
    async_playwright, Browser, BrowserContext, Page, Playwright
)

log = logging.getLogger("core.browser")

COOKIES_DIR = Path("output/cookies")

# Realistic screen resolutions
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 720},
]

# JS to inject into every page — hides all automation fingerprints
STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Fake plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin',    filename: 'internal-pdf-viewer',  description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer',    filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client',        filename: 'internal-nacl-plugin',  description: '' },
        ];
        arr.item = i => arr[i];
        arr.namedItem = n => arr.find(p => p.name === n);
        arr.refresh = () => {};
        Object.setPrototypeOf(arr, PluginArray.prototype);
        return arr;
    }
});

// Fake languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'hi'] });

// Fix permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);

// Chrome runtime
window.chrome = {
    runtime: {
        onMessage: { addListener: () => {}, removeListener: () => {} },
        connect: () => ({ onMessage: { addListener: () => {} }, postMessage: () => {} }),
        sendMessage: () => {},
        id: 'nmmhkkegccagdldgiimedpiccmgmieda',
    },
    loadTimes: () => ({
        commitLoadTime:      performance.now() / 1000 - Math.random() * 2,
        connectionInfo:      'h2',
        finishDocumentLoadTime: performance.now() / 1000,
        finishLoadTime:      performance.now() / 1000,
        firstPaintAfterLoadTime: 0,
        firstPaintTime:      performance.now() / 1000,
        navigationType:      'Other',
        npnNegotiatedProtocol: 'h2',
        requestTime:         performance.now() / 1000 - Math.random() * 3,
        startLoadTime:       performance.now() / 1000 - Math.random() * 3,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy:   true,
        wasNpnNegotiated:    true,
    }),
    csi: () => ({ onloadT: Date.now(), pageT: performance.now(), startE: Date.now() - 1000, tran: 15 }),
    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
};

// WebGL fingerprint randomisation
const getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParam.call(this, parameter);
};

// Canvas fingerprint noise
const toDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    if (type === 'image/png' && this.width === 220 && this.height === 30) {
        const ctx = this.getContext('2d');
        const imageData = ctx.getImageData(0, 0, this.width, this.height);
        for (let i = 0; i < imageData.data.length; i += 100) {
            imageData.data[i] += Math.floor(Math.random() * 2);
        }
        ctx.putImageData(imageData, 0, 0);
    }
    return toDataURL.apply(this, arguments);
};

// Battery API spoof
Object.defineProperty(navigator, 'getBattery', {
    value: () => Promise.resolve({
        charging: true, chargingTime: 0, dischargingTime: Infinity,
        level: 0.98 + Math.random() * 0.02,
        addEventListener: () => {},
    })
});

// Hardware concurrency
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// Connection spoof
Object.defineProperty(navigator, 'connection', {
    get: () => ({
        downlink: 10 + Math.random() * 5,
        effectiveType: '4g',
        rtt: 50 + Math.floor(Math.random() * 50),
        saveData: false,
        addEventListener: () => {},
    })
});
"""


class BrowserSession:
    """
    Manages one Chromium browser instance shared across all agents.
    Features: full anti-detection, cookie persistence per portal,
    human-like delays, viewport randomisation.
    """

    def __init__(self, headless: bool = True, persist_cookies: bool = True):
        self.headless        = headless
        self.persist_cookies = persist_cookies
        self._pw:      Playwright | None = None
        self._browser: Browser    | None = None
        self._ua = UserAgent()
        COOKIES_DIR.mkdir(parents=True, exist_ok=True)

    async def start(self):
        self._pw = await async_playwright().start()

        # Extra launch args to beat bot-detection
        launch_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-infobars",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-translate",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--use-mock-keychain",
            "--disable-background-networking",
            "--disable-client-side-phishing-detection",
            "--disable-hang-monitor",
            "--disable-prompt-on-repost",
            "--disable-domain-reliability",
            "--disable-features=IsolateOrigins,site-per-process,TranslateUI",
            "--metrics-recording-only",
            "--safebrowsing-disable-auto-update",
            f"--window-size={random.choice(VIEWPORTS)['width']},{random.choice(VIEWPORTS)['height']}",
        ]

        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )
        log.info("[browser] Chromium launched (stealth mode)")

    async def new_context(self, portal_id: str = "") -> BrowserContext:
        """Create a fresh browser context with full stealth headers."""
        vp   = random.choice(VIEWPORTS)
        ua   = self._ua.chrome

        ctx = await self._browser.new_context(
            viewport=vp,
            user_agent=ua,
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            geolocation={"latitude": 28.6139, "longitude": 77.2090},  # New Delhi
            permissions=["geolocation"],
            color_scheme="light",
            extra_http_headers={
                "Accept-Language":           "en-IN,en-US;q=0.9,en;q=0.8,hi;q=0.7",
                "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Encoding":           "gzip, deflate, br",
                "Sec-Fetch-Dest":            "document",
                "Sec-Fetch-Mode":            "navigate",
                "Sec-Fetch-Site":            "none",
                "Sec-Fetch-User":            "?1",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control":             "max-age=0",
            },
            ignore_https_errors=True,  # Many govt portals have expired SSL certs
        )

        # Inject stealth JS on every new page
        await ctx.add_init_script(STEALTH_JS)

        # Restore saved cookies for this portal
        if self.persist_cookies and portal_id:
            await self._load_cookies(ctx, portal_id)

        return ctx

    async def new_page(self, context: BrowserContext | None = None, portal_id: str = "") -> Page:
        """Create a stealth page."""
        ctx  = context or await self.new_context(portal_id=portal_id)
        page = await ctx.new_page()

        # Override navigator properties at page level too
        await page.add_init_script(STEALTH_JS)

        # Block unnecessary resources to speed up scraping
        await page.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,eot}", _block_resource)
        await page.route("**/google-analytics.com/**", _block_resource)
        await page.route("**/googletagmanager.com/**", _block_resource)
        await page.route("**/facebook.com/tr**",       _block_resource)
        await page.route("**/doubleclick.net/**",       _block_resource)

        return page

    async def save_cookies(self, context: BrowserContext, portal_id: str):
        """Persist cookies so next session reuses them (avoids re-login)."""
        if not self.persist_cookies or not portal_id:
            return
        try:
            cookies = await context.cookies()
            path = COOKIES_DIR / f"{portal_id}.json"
            path.write_text(json.dumps(cookies, indent=2))
            log.debug(f"[browser] Saved {len(cookies)} cookies for {portal_id}")
        except Exception as e:
            log.debug(f"[browser] Cookie save failed: {e}")

    async def _load_cookies(self, context: BrowserContext, portal_id: str):
        """Load persisted cookies into context."""
        path = COOKIES_DIR / f"{portal_id}.json"
        if not path.exists():
            return
        try:
            cookies = json.loads(path.read_text())
            await context.add_cookies(cookies)
            log.debug(f"[browser] Loaded {len(cookies)} cookies for {portal_id}")
        except Exception as e:
            log.debug(f"[browser] Cookie load failed: {e}")

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


async def _block_resource(route):
    """Block unnecessary resources to speed up page loads."""
    await route.abort()


async def random_delay(min_s: float = 2.0, max_s: float = 5.0):
    """Human-like delay between requests."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_scroll(page: Page, scrolls: int = 3):
    """Simulate human scrolling to trigger lazy-loaded content."""
    for _ in range(scrolls):
        await page.mouse.wheel(0, random.randint(300, 700))
        await asyncio.sleep(random.uniform(0.3, 0.8))


async def wait_for_content(page: Page, timeout: int = 30_000):
    """Wait for page to fully load with fallback."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:
            await asyncio.sleep(3)
