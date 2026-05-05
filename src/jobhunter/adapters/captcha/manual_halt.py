"""Manual-halt captcha adapter.

v1 policy: never solve captchas programmatically. If one is spotted we:
1. Take a screenshot for the operator.
2. Raise `AuthError` so the pipeline halts cleanly.
3. (Phase 3) The orchestrator wires this into a Telegram alert so the
   user sees it on their phone.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from jobhunter.core.errors import AuthError
from jobhunter.ports.browser import BrowserPage
from jobhunter.ports.captcha import CaptchaOutcome


class ManualHaltCaptchaSolver:
    name = "manual_halt"

    def __init__(self, *, screenshot_dir: Path = Path("data/captchas")) -> None:
        self._dir = screenshot_dir

    async def detect(self, page: BrowserPage) -> bool:
        # Common captcha frames + iframes. Portal adapters can also call
        # `solve()` directly when they know the marker.
        markers = [
            "iframe[src*='captcha']",
            "iframe[src*='recaptcha']",
            "div.g-recaptcha",
            "div[class*='captcha']",
            "[id*='captcha']",
        ]
        for sel in markers:
            if await page.is_visible(sel, timeout_ms=500):
                return True
        return False

    async def solve(self, page: BrowserPage) -> CaptchaOutcome:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        path = self._dir / f"captcha-{ts}.png"
        try:
            await page.screenshot(str(path))
        except Exception as e:  # noqa: BLE001
            logger.warning("captcha screenshot failed: {}", e)
        logger.error("captcha detected at {}; pipeline halting", page.url)
        raise AuthError(
            f"captcha detected on {page.url}. Screenshot: {path}. "
            f"Solve manually in a real browser, then resume."
        )
