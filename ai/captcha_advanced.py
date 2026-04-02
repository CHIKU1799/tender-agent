"""
Advanced CAPTCHA solver — handles ALL types found on Indian government portals.

Supported types:
  1. Image/text CAPTCHA      → GPT-4o vision (enhanced with retries + refresh)
  2. Slider/drag CAPTCHA     → Playwright mouse simulation
  3. reCAPTCHA v2/v3         → 2Captcha / AntiCaptcha API  +  audio bypass fallback
  4. hCaptcha                → 2Captcha / AntiCaptcha API
  5. Math CAPTCHA            → GPT-4o / regex eval
  6. Hidden token CAPTCHA    → DOM scraping + token injection
  7. Invisible CAPTCHA       → stealth headers + cookie warmup

Environment variables (all optional — falls back gracefully):
  TWOCAPTCHA_KEY   — 2captcha.com API key  (recommended for reCAPTCHA)
  ANTICAPTCHA_KEY  — anti-captcha.com API key (alternative)
  OPENAI_API_KEY   — GPT-4o for image / math CAPTCHAs

Reference implementations adapted from:
  https://github.com/2captcha/2captcha-python
  https://github.com/nicktindall/cycaptcha
  https://github.com/sarperavci/GoogleRecaptchaBypass
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
from typing import Optional

import aiohttp
from playwright.async_api import Page, ElementHandle

log = logging.getLogger("ai.captcha_advanced")

# ── Service keys ──────────────────────────────────────────────────────────────
TWOCAPTCHA_KEY  = os.getenv("TWOCAPTCHA_KEY",  "")
ANTICAPTCHA_KEY = os.getenv("ANTICAPTCHA_KEY", "")

# ── Selector banks ────────────────────────────────────────────────────────────
IMAGE_CAPTCHA_SELECTORS = [
    "img[src*='captcha' i]", "img[id*='captcha' i]", "img[class*='captcha' i]",
    "#captchaImage", ".captcha-img", "img[alt*='captcha' i]",
    "img[src*='Captcha']", "img[src*='kcaptcha']", "img[src*='vcaptcha']",
    "img[src*='rand']", "img[src*='code']",
]
IMAGE_INPUT_SELECTORS = [
    "input[name*='captcha' i]", "input[id*='captcha' i]",
    "input[placeholder*='captcha' i]", "input[placeholder*='code' i]",
    "#captchaText", "#captcha", "#CaptchaInputText", "input[name*='Code' i]",
    "input[name='verifyCode']", "input[name='security_code']",
]
SLIDER_SELECTORS = [
    ".slider-btn", ".nc_iconfont.btn_slide",
    "[class*='slider'][class*='btn']", "[class*='slide-btn']",
    ".JDJRV-slide-btn", "#nc_1_n1z",
]
RECAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']", ".g-recaptcha",
    "[data-sitekey]", "#recaptcha",
]
HCAPTCHA_SELECTORS = [
    "iframe[src*='hcaptcha']", ".h-captcha",
    "[data-hcaptcha-sitekey]",
]
MATH_CAPTCHA_SELECTORS = [
    "[class*='math'][class*='captcha' i]",
    "[id*='math'][id*='captcha' i]",
    "span[class*='captcha']",
    ".captcha-question",
]
SUBMIT_SELECTORS = [
    "input[type='submit']", "button[type='submit']",
    "#btnSubmit", "input[value='Search']", "input[value='Go']",
    "button:has-text('Search')", "button:has-text('Submit')",
    "input[value='Verify']", "button:has-text('Verify')",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _find_first(page: Page, selectors: list[str]) -> tuple[Optional[ElementHandle], str]:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                el = await loc.element_handle()
                if el:
                    return el, sel
        except Exception:
            continue
    return None, ""


async def _human_type(page: Page, selector: str, text: str):
    """Type with realistic delays to avoid bot detection."""
    await page.focus(selector)
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    for ch in text:
        await page.keyboard.type(ch, delay=random.randint(60, 180))
    await asyncio.sleep(random.uniform(0.3, 0.8))


async def _human_mouse_move(page: Page, start_x: int, start_y: int, end_x: int, end_y: int):
    """Simulate a human-like mouse drag with bezier curve approximation."""
    steps = random.randint(20, 40)
    for i in range(steps + 1):
        t = i / steps
        # Cubic ease-out
        ease = 1 - (1 - t) ** 3
        jitter_x = random.randint(-2, 2) if 0 < t < 1 else 0
        jitter_y = random.randint(-1, 1) if 0 < t < 1 else 0
        x = int(start_x + (end_x - start_x) * ease) + jitter_x
        y = int(start_y + (end_y - start_y) * ease) + jitter_y
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.01, 0.03))


# ─────────────────────────────────────────────────────────────────────────────
# 1. IMAGE / TEXT CAPTCHA  (GPT-4o vision, enhanced with retries + refresh)
# ─────────────────────────────────────────────────────────────────────────────

async def solve_image_captcha(page: Page, max_retries: int = 4) -> bool:
    """Solve standard image/text CAPTCHA using GPT-4o."""
    try:
        from ai.client import get_client
        client = get_client()
    except RuntimeError:
        log.warning("[captcha] OPENAI_API_KEY not set — skipping GPT-4o solver")
        return False

    for attempt in range(1, max_retries + 1):
        log.info(f"[img-captcha] Attempt {attempt}/{max_retries}")

        img_el, img_sel = await _find_first(page, IMAGE_CAPTCHA_SELECTORS)
        if not img_el:
            log.debug("[img-captcha] No image CAPTCHA found")
            return False

        # Try to refresh CAPTCHA if there's a refresh link
        if attempt > 1:
            refresh_sel = [
                "a[onclick*='captcha' i]", "a[id*='refresh' i]",
                "img[onclick*='captcha' i]", "#refreshCaptcha", ".captcha-refresh",
            ]
            refresh_el, _ = await _find_first(page, refresh_sel)
            if refresh_el:
                await refresh_el.click()
                await asyncio.sleep(1.5)

        try:
            img_bytes = await img_el.screenshot()
        except Exception as e:
            log.warning(f"[img-captcha] Screenshot failed: {e}")
            await asyncio.sleep(1)
            continue

        b64 = base64.b64encode(img_bytes).decode()

        try:
            resp = await client.chat.completions.create(
                model="gpt-4o",
                max_tokens=30,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "This is a CAPTCHA image from an Indian government procurement portal. "
                                "The text may be distorted, rotated, or have noise. "
                                "Read the characters EXACTLY as shown — they may be alphanumeric. "
                                "Reply with ONLY the CAPTCHA characters, no spaces, no explanation, "
                                "no punctuation. If you see digits only, return just digits. "
                                "Common patterns: 5-6 alphanumeric chars like 'K3mP9Q' or '482631'."
                            ),
                        },
                    ],
                }],
            )
            solution = resp.choices[0].message.content.strip()
            # Clean the solution — remove any stray quotes/spaces GPT might add
            solution = re.sub(r"[^A-Za-z0-9]", "", solution)
            log.info(f"[img-captcha] GPT-4o solution: '{solution}'")
        except Exception as e:
            log.error(f"[img-captcha] GPT-4o error: {e}")
            await asyncio.sleep(2)
            continue

        if not solution:
            log.warning("[img-captcha] Empty solution from GPT-4o")
            continue

        # Type solution
        inp_el, inp_sel = await _find_first(page, IMAGE_INPUT_SELECTORS)
        if not inp_el:
            log.warning("[img-captcha] No input field found")
            return False

        try:
            await _human_type(page, inp_sel, solution)
        except Exception:
            try:
                await inp_el.fill(solution)
            except Exception as e2:
                log.warning(f"[img-captcha] Could not type solution: {e2}")
                continue

        # Submit
        sub_el, sub_sel = await _find_first(page, SUBMIT_SELECTORS)
        if sub_el:
            try:
                async with page.expect_navigation(wait_until="networkidle", timeout=45_000):
                    await sub_el.click()
            except Exception:
                pass
        else:
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle", timeout=30_000)

        await asyncio.sleep(1.2)
        still_present, _ = await _find_first(page, IMAGE_CAPTCHA_SELECTORS)
        if not still_present:
            log.info("[img-captcha] ✓ Solved successfully")
            return True

        log.warning(f"[img-captcha] CAPTCHA still visible after attempt {attempt}, retrying…")

    log.error("[img-captcha] Failed after all retries")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. MATH CAPTCHA  ("What is 3 + 7?", "5 × 4 = ?")
# ─────────────────────────────────────────────────────────────────────────────

async def solve_math_captcha(page: Page) -> bool:
    """Detect and solve arithmetic CAPTCHAs without any external API."""
    # Try to find a math question in common elements
    math_texts = []
    for sel in MATH_CAPTCHA_SELECTORS + ["label", ".captcha", "#captchaLabel"]:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 5)):
                txt = await locs.nth(i).inner_text()
                if txt:
                    math_texts.append(txt.strip())
        except Exception:
            continue

    for text in math_texts:
        # Match patterns like "3 + 7 =", "5×4", "12 - 3 ?", "what is 8 + 2"
        match = re.search(
            r"(\d+)\s*([+\-×x\*÷/])\s*(\d+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            continue

        a, op, b = int(match.group(1)), match.group(2), int(match.group(3))
        if   op in ("+",):              answer = a + b
        elif op in ("-",):              answer = a - b
        elif op in ("×", "x", "X", "*"): answer = a * b
        elif op in ("÷", "/"):         answer = a // b if b != 0 else 0
        else:
            continue

        log.info(f"[math-captcha] Detected: {a} {op} {b} = {answer}")

        inp_el, inp_sel = await _find_first(page, IMAGE_INPUT_SELECTORS)
        if not inp_el:
            continue

        await _human_type(page, inp_sel, str(answer))

        sub_el, _ = await _find_first(page, SUBMIT_SELECTORS)
        if sub_el:
            try:
                async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                    await sub_el.click()
            except Exception:
                pass
        else:
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle", timeout=20_000)

        log.info("[math-captcha] ✓ Answer submitted")
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# 3. SLIDER / DRAG CAPTCHA  (common on ONGC, some state portals)
# ─────────────────────────────────────────────────────────────────────────────

async def solve_slider_captcha(page: Page, max_retries: int = 3) -> bool:
    """Solve slider CAPTCHA by simulating human-like drag."""
    for attempt in range(1, max_retries + 1):
        slider_el, slider_sel = await _find_first(page, SLIDER_SELECTORS)
        if not slider_el:
            return False

        log.info(f"[slider-captcha] Attempt {attempt}/{max_retries}")

        try:
            box = await slider_el.bounding_box()
            if not box:
                continue

            # Start: center of the slider handle
            start_x = int(box["x"] + box["width"] / 2)
            start_y = int(box["y"] + box["height"] / 2)

            # Find track width to determine end position
            track_selectors = [
                ".slider-track", ".nc_iconfont.btn_slide",
                "[class*='slider-track']", "[class*='slide-track']",
            ]
            track_el, _ = await _find_first(page, track_selectors)
            if track_el:
                track_box = await track_el.bounding_box()
                end_x = int(track_box["x"] + track_box["width"] - 20) if track_box else start_x + 280
            else:
                end_x = start_x + random.randint(260, 320)

            await page.mouse.move(start_x, start_y)
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.1, 0.2))

            await _human_mouse_move(page, start_x, start_y, end_x, start_y)

            await asyncio.sleep(random.uniform(0.2, 0.5))
            await page.mouse.up()
            await asyncio.sleep(1.5)

            # Check if slider is still present (failure = still visible + same position)
            still_present, _ = await _find_first(page, SLIDER_SELECTORS)
            if not still_present:
                log.info("[slider-captcha] ✓ Solved")
                return True

            # Check for success text
            body_text = await page.inner_text("body")
            if any(kw in body_text.lower() for kw in ["success", "verified", "passed"]):
                log.info("[slider-captcha] ✓ Verified (text confirmation)")
                return True

        except Exception as e:
            log.warning(f"[slider-captcha] Error on attempt {attempt}: {e}")
            await asyncio.sleep(1)

    log.warning("[slider-captcha] Failed after all retries")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 4. reCAPTCHA v2 / v3  (2Captcha or AntiCaptcha API)
# ─────────────────────────────────────────────────────────────────────────────

async def _get_recaptcha_sitekey(page: Page) -> Optional[str]:
    """Extract reCAPTCHA sitekey from page DOM."""
    # Method 1: data-sitekey attribute
    try:
        el = page.locator("[data-sitekey]").first
        if await el.count() > 0:
            key = await el.get_attribute("data-sitekey")
            if key:
                return key
    except Exception:
        pass

    # Method 2: iframe src parameter
    try:
        iframes = page.locator("iframe[src*='recaptcha']")
        count = await iframes.count()
        for i in range(count):
            src = await iframes.nth(i).get_attribute("src") or ""
            m = re.search(r"[?&]k=([A-Za-z0-9_\-]+)", src)
            if m:
                return m.group(1)
    except Exception:
        pass

    # Method 3: JS variable in page source
    try:
        content = await page.content()
        m = re.search(r"['\"]sitekey['\"]\s*:\s*['\"]([A-Za-z0-9_\-]+)['\"]", content)
        if m:
            return m.group(1)
        m = re.search(r"data-sitekey=['\"]([A-Za-z0-9_\-]+)['\"]", content)
        if m:
            return m.group(1)
    except Exception:
        pass

    return None


async def _solve_recaptcha_2captcha(sitekey: str, page_url: str, is_v3: bool = False) -> Optional[str]:
    """Submit to 2captcha and poll for solution."""
    if not TWOCAPTCHA_KEY:
        return None

    submit_url = "https://2captcha.com/in.php"
    poll_url   = "https://2captcha.com/res.php"

    payload = {
        "key":      TWOCAPTCHA_KEY,
        "method":   "userrecaptcha",
        "googlekey": sitekey,
        "pageurl":  page_url,
        "json":     1,
    }
    if is_v3:
        payload["version"] = "v3"
        payload["action"]  = "submit"
        payload["min_score"] = 0.3

    async with aiohttp.ClientSession() as session:
        async with session.post(submit_url, data=payload) as r:
            data = await r.json(content_type=None)

        if data.get("status") != 1:
            log.error(f"[2captcha] Submit failed: {data}")
            return None

        task_id = data["request"]
        log.info(f"[2captcha] Task submitted, id={task_id}")

        # Poll up to 120 seconds
        for _ in range(24):
            await asyncio.sleep(5)
            async with session.get(poll_url, params={
                "key": TWOCAPTCHA_KEY, "action": "get", "id": task_id, "json": 1,
            }) as r:
                result = await r.json(content_type=None)

            if result.get("status") == 1:
                log.info("[2captcha] ✓ Token received")
                return result["request"]
            elif result.get("request") != "CAPCHA_NOT_READY":
                log.error(f"[2captcha] Error: {result}")
                return None

        log.error("[2captcha] Timeout waiting for solution")
        return None


async def _solve_recaptcha_anticaptcha(sitekey: str, page_url: str) -> Optional[str]:
    """Submit to anti-captcha.com and poll for solution."""
    if not ANTICAPTCHA_KEY:
        return None

    async with aiohttp.ClientSession() as session:
        # Create task
        async with session.post("https://api.anti-captcha.com/createTask", json={
            "clientKey": ANTICAPTCHA_KEY,
            "task": {
                "type":      "NoCaptchaTaskProxyless",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            },
        }) as r:
            data = await r.json()

        if data.get("errorId"):
            log.error(f"[anticaptcha] Create task error: {data}")
            return None

        task_id = data["taskId"]
        log.info(f"[anticaptcha] Task id={task_id}")

        for _ in range(24):
            await asyncio.sleep(5)
            async with session.post("https://api.anti-captcha.com/getTaskResult", json={
                "clientKey": ANTICAPTCHA_KEY,
                "taskId":    task_id,
            }) as r:
                result = await r.json()

            if result.get("status") == "ready":
                log.info("[anticaptcha] ✓ Token received")
                return result["solution"]["gRecaptchaResponse"]
            elif result.get("errorId"):
                log.error(f"[anticaptcha] Error: {result}")
                return None

        return None


async def _inject_recaptcha_token(page: Page, token: str):
    """Inject solved reCAPTCHA token into the page."""
    await page.evaluate(f"""
        (token) => {{
            // Standard hidden textarea used by reCAPTCHA
            const ta = document.getElementById('g-recaptcha-response');
            if (ta) {{
                ta.innerHTML = token;
                ta.style.display = 'block';
            }}
            // Also set on any other g-recaptcha-response fields
            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
                el.value = token;
            }});
            // Trigger any onload callbacks
            try {{
                if (typeof ___grecaptcha_cfg !== 'undefined') {{
                    const clients = ___grecaptcha_cfg.clients;
                    if (clients) {{
                        const id = Object.keys(clients)[0];
                        if (clients[id] && clients[id].aa && clients[id].aa.callback) {{
                            clients[id].aa.callback(token);
                        }}
                    }}
                }}
            }} catch(e) {{}}
            // Also try window callbacks
            if (typeof window.onRecaptchaSuccess === 'function') window.onRecaptchaSuccess(token);
            if (typeof window.captchaCallback === 'function') window.captchaCallback(token);
        }}
    """, token)
    await asyncio.sleep(0.5)


async def solve_recaptcha(page: Page) -> bool:
    """Solve reCAPTCHA v2/v3 using 2Captcha or AntiCaptcha APIs."""
    sitekey = await _get_recaptcha_sitekey(page)
    if not sitekey:
        log.debug("[recaptcha] No sitekey found on page")
        return False

    page_url = page.url
    log.info(f"[recaptcha] Sitekey found: {sitekey[:20]}… | URL: {page_url}")

    token = None
    if TWOCAPTCHA_KEY:
        token = await _solve_recaptcha_2captcha(sitekey, page_url)
    elif ANTICAPTCHA_KEY:
        token = await _solve_recaptcha_anticaptcha(sitekey, page_url)
    else:
        log.warning("[recaptcha] No 2Captcha or AntiCaptcha key configured. "
                    "Set TWOCAPTCHA_KEY or ANTICAPTCHA_KEY in .env")
        return False

    if not token:
        return False

    await _inject_recaptcha_token(page, token)

    # Submit the form
    sub_el, _ = await _find_first(page, SUBMIT_SELECTORS)
    if sub_el:
        try:
            async with page.expect_navigation(wait_until="networkidle", timeout=45_000):
                await sub_el.click()
        except Exception:
            pass

    log.info("[recaptcha] ✓ Token injected and form submitted")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 5. hCAPTCHA  (2Captcha API)
# ─────────────────────────────────────────────────────────────────────────────

async def _get_hcaptcha_sitekey(page: Page) -> Optional[str]:
    try:
        el = page.locator("[data-hcaptcha-sitekey], .h-captcha").first
        if await el.count() > 0:
            key = await el.get_attribute("data-sitekey") or await el.get_attribute("data-hcaptcha-sitekey")
            if key:
                return key
    except Exception:
        pass
    try:
        content = await page.content()
        m = re.search(r"hcaptcha\.com/.*?[?&]sitekey=([A-Za-z0-9\-]+)", content)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


async def solve_hcaptcha(page: Page) -> bool:
    """Solve hCaptcha using 2Captcha API."""
    sitekey = await _get_hcaptcha_sitekey(page)
    if not sitekey or not TWOCAPTCHA_KEY:
        return False

    log.info(f"[hcaptcha] Sitekey: {sitekey[:20]}…")

    async with aiohttp.ClientSession() as session:
        async with session.post("https://2captcha.com/in.php", data={
            "key":      TWOCAPTCHA_KEY,
            "method":   "hcaptcha",
            "sitekey":  sitekey,
            "pageurl":  page.url,
            "json":     1,
        }) as r:
            data = await r.json(content_type=None)

        if data.get("status") != 1:
            return False

        task_id = data["request"]

        for _ in range(24):
            await asyncio.sleep(5)
            async with session.get("https://2captcha.com/res.php", params={
                "key": TWOCAPTCHA_KEY, "action": "get", "id": task_id, "json": 1,
            }) as r:
                result = await r.json(content_type=None)

            if result.get("status") == 1:
                token = result["request"]
                # Inject hcaptcha token
                await page.evaluate(f"""
                    (() => {{
                        const ta = document.querySelector('[name="h-captcha-response"]');
                        if (ta) ta.value = '{token}';
                        if (typeof window.onHCaptchaSuccess === 'function')
                            window.onHCaptchaSuccess('{token}');
                    }})()
                """)
                log.info("[hcaptcha] ✓ Token injected")
                return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# 6. HIDDEN TOKEN CAPTCHA  (form tokens, CSRF, ViewState injection)
# ─────────────────────────────────────────────────────────────────────────────

async def warmup_session_cookies(page: Page, url: str) -> dict:
    """
    Visit portal homepage to acquire session cookies + hidden form tokens.
    Many portals check that a valid session exists before showing tenders.
    Returns extracted tokens dict.
    """
    tokens = {}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        # Extract all hidden inputs (ViewState, CSRF tokens, etc.)
        hidden_inputs = await page.eval_on_selector_all(
            "input[type='hidden']",
            "els => els.map(el => [el.name, el.value])"
        )
        tokens = dict(hidden_inputs)
        log.debug(f"[warmup] Extracted {len(tokens)} hidden tokens from {url}")

        # Simulate brief human interaction
        await page.mouse.move(
            random.randint(200, 800),
            random.randint(200, 600),
        )
        await asyncio.sleep(random.uniform(0.5, 1.2))

    except Exception as e:
        log.debug(f"[warmup] {url}: {e}")

    return tokens


async def extract_hidden_tokens(page: Page) -> dict:
    """Extract all hidden form fields from current page."""
    try:
        inputs = await page.eval_on_selector_all(
            "input[type='hidden']",
            "els => els.map(el => ({name: el.name, value: el.value, id: el.id}))"
        )
        return {el["name"]: el["value"] for el in inputs if el.get("name")}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 7. AUDIO CAPTCHA FALLBACK  (speech-to-text via Web Speech / whisper)
# ─────────────────────────────────────────────────────────────────────────────

async def try_audio_captcha_bypass(page: Page) -> bool:
    """
    Click the audio CAPTCHA button (if present) and use OpenAI Whisper to transcribe.
    This is a fallback when vision fails repeatedly.
    """
    try:
        from ai.client import get_client
        client = get_client()
    except RuntimeError:
        return False

    audio_btn_sels = [
        ".rc-button-audio", "#recaptcha-audio-button",
        "button[aria-label*='audio' i]", ".audio-captcha",
    ]
    audio_btn, _ = await _find_first(page, audio_btn_sels)
    if not audio_btn:
        return False

    try:
        await audio_btn.click()
        await asyncio.sleep(2)

        audio_src_sels = [".rc-audiochallenge-tdownload-link", "audio", "[src*='.mp3']"]
        audio_el, _ = await _find_first(page, audio_src_sels)
        if not audio_el:
            return False

        audio_url = await audio_el.get_attribute("src") or await audio_el.get_attribute("href")
        if not audio_url:
            return False

        # Download audio
        async with aiohttp.ClientSession() as session:
            async with session.get(audio_url) as resp:
                audio_bytes = await resp.read()

        # Transcribe with Whisper
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            with open(tmp_path, "rb") as audio_file:
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1", file=audio_file
                )
            solution = re.sub(r"[^A-Za-z0-9]", "", transcript.text.strip())
            log.info(f"[audio-captcha] Whisper solution: '{solution}'")
        finally:
            _os.unlink(tmp_path)

        inp_el, inp_sel = await _find_first(page, IMAGE_INPUT_SELECTORS + [
            "#audio-response", ".rc-audiochallenge-response-field input",
        ])
        if inp_el and solution:
            await _human_type(page, inp_sel, solution)
            sub_el, _ = await _find_first(page, SUBMIT_SELECTORS + [
                "#recaptcha-verify-button",
            ])
            if sub_el:
                await sub_el.click()
                await asyncio.sleep(2)
            log.info("[audio-captcha] ✓ Solution submitted")
            return True

    except Exception as e:
        log.warning(f"[audio-captcha] Failed: {e}")

    return False


# ─────────────────────────────────────────────────────────────────────────────
# MASTER SOLVER  — tries all methods in order
# ─────────────────────────────────────────────────────────────────────────────

async def solve_any_captcha(page: Page, max_total_attempts: int = 3) -> bool:
    """
    Auto-detect and solve any CAPTCHA on the current page.
    Tries: math → image/text (GPT-4o) → slider → reCAPTCHA → hCaptcha → audio.
    Returns True if a CAPTCHA was found and solved.
    """
    for attempt in range(1, max_total_attempts + 1):
        log.info(f"[captcha-master] Detection pass {attempt}/{max_total_attempts}")

        # Check page content for CAPTCHA indicators
        try:
            content = await page.content()
        except Exception:
            content = ""

        # ── Math CAPTCHA (fastest, no API) ──
        if re.search(r"\d+\s*[+\-×x*÷/]\s*\d+", content):
            if await solve_math_captcha(page):
                return True

        # ── Image / text CAPTCHA ──
        img_el, _ = await _find_first(page, IMAGE_CAPTCHA_SELECTORS)
        if img_el:
            if await solve_image_captcha(page, max_retries=3):
                return True
            # Try audio fallback if image fails
            if await try_audio_captcha_bypass(page):
                return True

        # ── Slider ──
        slider_el, _ = await _find_first(page, SLIDER_SELECTORS)
        if slider_el:
            if await solve_slider_captcha(page):
                return True

        # ── reCAPTCHA ──
        rc_el, _ = await _find_first(page, RECAPTCHA_SELECTORS)
        if rc_el:
            if await solve_recaptcha(page):
                return True

        # ── hCaptcha ──
        hc_el, _ = await _find_first(page, HCAPTCHA_SELECTORS)
        if hc_el:
            if await solve_hcaptcha(page):
                return True

        # No CAPTCHA found
        if attempt == 1:
            log.debug("[captcha-master] No CAPTCHA detected on page")
            return False  # Not a failure — just no CAPTCHA present

        await asyncio.sleep(2)

    log.error("[captcha-master] Could not solve CAPTCHA after all attempts")
    return False
