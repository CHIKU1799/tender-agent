"""
AI CAPTCHA solver using GPT-4o vision.

Flow:
  1. Locate CAPTCHA image element on the page
  2. Screenshot it → base64 PNG
  3. Send to GPT-4o with vision prompt → returns solved text
  4. Type the solution into the CAPTCHA input field
  5. Submit the form
"""
from __future__ import annotations
import asyncio
import base64
import logging
from playwright.async_api import Page
from ai.client import get_client

log = logging.getLogger("ai.captcha")

# CSS selectors to try for captcha image
CAPTCHA_IMG_SELECTORS = [
    "img[src*='captcha']",
    "img[src*='Captcha']",
    "img[id*='captcha']",
    "img[id*='Captcha']",
    "#captchaImage",
    ".captcha img",
    "img[alt*='captcha' i]",
]

# CSS selectors to try for the captcha text input
CAPTCHA_INPUT_SELECTORS = [
    "input[name*='captcha' i]",
    "input[id*='captcha' i]",
    "input[placeholder*='captcha' i]",
    "#captchaText",
    "#captcha",
    "input[type='text'][name*='Code' i]",
]

# Submit button selectors
SUBMIT_SELECTORS = [
    "input[type='submit']",
    "button[type='submit']",
    "#btnSubmit",
    "input[value='Search']",
    "input[value='Go']",
    "button:has-text('Search')",
    "button:has-text('Submit')",
]


async def _find_element(page: Page, selectors: list[str]):
    """Return the first matching element from a list of selectors."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                return el, sel
        except Exception:
            continue
    return None, None


async def solve_and_submit(
    page: Page,
    max_retries: int = 3,
    submit: bool = True,
) -> bool:
    """
    Find CAPTCHA on the current page, solve it with GPT-4o, type the answer,
    and optionally click submit.

    Returns True if solved successfully, False otherwise.
    """
    client = get_client()

    for attempt in range(1, max_retries + 1):
        log.info(f"[captcha] Solve attempt {attempt}/{max_retries}")

        # 1. Find and screenshot the captcha image
        img_el, img_sel = await _find_element(page, CAPTCHA_IMG_SELECTORS)
        if img_el is None:
            log.warning("[captcha] No CAPTCHA image found on page")
            return False

        try:
            img_bytes = await img_el.screenshot()
        except Exception as e:
            log.warning(f"[captcha] Failed to screenshot CAPTCHA: {e}")
            await asyncio.sleep(1)
            continue

        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        log.info(f"[captcha] Captured CAPTCHA image ({len(img_bytes)} bytes), sending to GPT-4o")

        # 2. Ask GPT-4o to solve it
        try:
            resp = await client.chat.completions.create(
                model="gpt-4o",
                max_tokens=20,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_img}",
                                    "detail": "high",
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "This is a CAPTCHA image from an Indian government procurement portal. "
                                    "Read the characters shown exactly as they appear. "
                                    "Reply with ONLY the CAPTCHA text — no spaces, no punctuation, no explanation."
                                ),
                            },
                        ],
                    }
                ],
            )
            solution = resp.choices[0].message.content.strip()
            log.info(f"[captcha] GPT-4o solution: '{solution}'")
        except Exception as e:
            log.error(f"[captcha] GPT-4o API error: {e}")
            await asyncio.sleep(2)
            continue

        if not solution:
            log.warning("[captcha] GPT-4o returned empty solution")
            continue

        # 3. Type solution into input field
        inp_el, inp_sel = await _find_element(page, CAPTCHA_INPUT_SELECTORS)
        if inp_el is None:
            log.warning("[captcha] No CAPTCHA input field found")
            return False

        await inp_el.fill("")
        await inp_el.type(solution, delay=80)
        log.info(f"[captcha] Typed solution into '{inp_sel}'")

        # 4. Optionally submit
        if submit:
            sub_el, sub_sel = await _find_element(page, SUBMIT_SELECTORS)
            if sub_el:
                try:
                    async with page.expect_navigation(wait_until="networkidle", timeout=45_000):
                        await sub_el.click()
                    log.info(f"[captcha] Clicked submit '{sub_sel}'")
                except Exception as e:
                    log.warning(f"[captcha] Navigation after submit failed: {e}")
            else:
                await inp_el.press("Enter")
                await page.wait_for_load_state("networkidle", timeout=30_000)

        # 5. Check if CAPTCHA is still present (failed solve)
        await asyncio.sleep(1)
        still_present, _ = await _find_element(page, CAPTCHA_IMG_SELECTORS)
        if still_present and submit:
            log.warning(f"[captcha] CAPTCHA still visible after submit — retrying (attempt {attempt})")
            continue

        log.info("[captcha] CAPTCHA solved successfully")
        return True

    log.error(f"[captcha] Failed to solve CAPTCHA after {max_retries} attempts")
    return False
