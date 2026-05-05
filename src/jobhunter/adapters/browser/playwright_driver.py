"""Playwright BrowserDriver adapter.

`playwright-stealth` patches the Page to hide common bot fingerprints
(navigator.webdriver, languages, hardwareConcurrency, ...). Real anti-bot
work happens in the portal adapter via `human_pause` between actions.
"""

from __future__ import annotations

import asyncio
import json
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from jobhunter.core.errors import PortalError

if TYPE_CHECKING:
    from jobhunter.bootstrap.config import Settings

try:  # playwright-stealth is optional; degrade gracefully if missing
    from playwright_stealth import Stealth  # type: ignore[import-not-found]
    _STEALTH_AVAILABLE = True
except ImportError:  # pragma: no cover
    Stealth = None  # type: ignore[assignment, misc]
    _STEALTH_AVAILABLE = False


class PlaywrightPage:
    """Adapter implementing the `BrowserPage` Protocol."""

    def __init__(
        self,
        page: Page,
        *,
        human_delay_min_ms: int,
        human_delay_max_ms: int,
    ) -> None:
        self._page = page
        self._delay_min = human_delay_min_ms
        self._delay_max = human_delay_max_ms

    @property
    def url(self) -> str:
        return self._page.url

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        await self._page.goto(url, wait_until=wait_until)  # type: ignore[arg-type]

    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None:
        await self._page.click(selector, timeout=timeout_ms)

    async def fill(self, selector: str, value: str) -> None:
        await self._page.fill(selector, value)

    async def type_text(self, selector: str, value: str, *, delay_ms: int = 25) -> None:
        await self._page.type(selector, value, delay=delay_ms)

    async def wait_for(self, selector: str, *, timeout_ms: int = 10000) -> None:
        await self._page.wait_for_selector(selector, timeout=timeout_ms)

    async def wait_for_url(self, pattern: str, *, timeout_ms: int = 10000) -> None:
        await self._page.wait_for_url(pattern, timeout=timeout_ms)  # type: ignore[arg-type]

    async def is_visible(self, selector: str, *, timeout_ms: int = 1000) -> bool:
        try:
            await self._page.wait_for_selector(
                selector, timeout=timeout_ms, state="visible"
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def text(self, selector: str) -> str | None:
        loc = self._page.locator(selector).first
        try:
            return (await loc.inner_text(timeout=2000)).strip()
        except Exception:  # noqa: BLE001
            return None

    async def query_all_attrs(self, selector: str, attr: str) -> list[str]:
        elements = self._page.locator(selector)
        n = await elements.count()
        out: list[str] = []
        for i in range(n):
            v = await elements.nth(i).get_attribute(attr)
            if v is not None:
                out.append(v)
        return out

    async def html(self) -> str:
        return await self._page.content()

    async def screenshot(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        await self._page.screenshot(path=path, full_page=True)

    async def evaluate(self, expression: str) -> Any:
        return await self._page.evaluate(expression)

    async def human_pause(self) -> None:
        ms = random.randint(self._delay_min, self._delay_max)
        await asyncio.sleep(ms / 1000)


class PlaywrightSession:
    """Owns a `BrowserContext` + a single `Page`. Persists storage state."""

    def __init__(
        self,
        context: BrowserContext,
        *,
        storage_state_path: str | None,
        human_delay_min_ms: int,
        human_delay_max_ms: int,
    ) -> None:
        self._context = context
        self._storage_state_path = storage_state_path
        self._delay_min = human_delay_min_ms
        self._delay_max = human_delay_max_ms

    @asynccontextmanager
    async def page(self):  # type: ignore[no-untyped-def]
        page = await self._context.new_page()
        if _STEALTH_AVAILABLE:
            try:
                await Stealth().apply_stealth_async(page)  # type: ignore[misc]
            except Exception as e:  # noqa: BLE001
                logger.debug("apply_stealth_async failed (non-fatal): {}", e)
        try:
            yield PlaywrightPage(
                page,
                human_delay_min_ms=self._delay_min,
                human_delay_max_ms=self._delay_max,
            )
        finally:
            await page.close()

    async def save_storage_state(self) -> None:
        if not self._storage_state_path:
            return
        path = Path(self._storage_state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(path))


class PlaywrightDriver:
    """Top-level driver. One instance per pipeline run."""

    def __init__(
        self,
        *,
        headless: bool,
        human_delay_min_ms: int = 250,
        human_delay_max_ms: int = 900,
    ) -> None:
        self._headless = headless
        self._delay_min = human_delay_min_ms
        self._delay_max = human_delay_max_ms

    @classmethod
    def from_settings(cls, settings: "Settings") -> "PlaywrightDriver":
        return cls(
            headless=settings.browser_headless,
            human_delay_min_ms=settings.browser_human_delay_ms_min,
            human_delay_max_ms=settings.browser_human_delay_ms_max,
        )

    @asynccontextmanager
    async def session(  # type: ignore[no-untyped-def]
        self,
        *,
        storage_state_path: str | None = None,
        headless: bool | None = None,
    ):
        try:
            pw = await async_playwright().start()
        except Exception as e:  # noqa: BLE001
            raise PortalError(
                f"Playwright failed to start ({type(e).__name__}: {e}). "
                f"Did you run `playwright install chromium`?"
            ) from e

        browser: Browser | None = None
        try:
            browser = await pw.chromium.launch(
                headless=self._headless if headless is None else headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            storage_arg = self._read_storage(storage_state_path)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 800},
                storage_state=storage_arg,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            session = PlaywrightSession(
                context,
                storage_state_path=storage_state_path,
                human_delay_min_ms=self._delay_min,
                human_delay_max_ms=self._delay_max,
            )
            try:
                yield session
                await session.save_storage_state()
            finally:
                await context.close()
        finally:
            if browser is not None:
                await browser.close()
            await pw.stop()

    @staticmethod
    def _read_storage(path: str | None) -> dict[str, Any] | None:
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("storage_state at {} is corrupt; starting fresh", path)
            return None
