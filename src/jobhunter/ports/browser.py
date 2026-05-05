"""BrowserDriver port — interactive automation contract.

Adapters: `playwright_driver` for v1, can swap to Selenium / a remote CDP
service later without touching portal adapters.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BrowserPage(Protocol):
    """Per-page surface used by portal adapters and auth strategies.

    A `BrowserPage` is a thin async wrapper over a Playwright Page; the
    methods are intentionally narrow so a different driver (Selenium,
    Patchright, ...) can implement the same shape later.
    """

    url: str

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None: ...
    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def type_text(self, selector: str, value: str, *, delay_ms: int = 25) -> None: ...
    async def wait_for(self, selector: str, *, timeout_ms: int = 10000) -> None: ...
    async def wait_for_url(self, pattern: str, *, timeout_ms: int = 10000) -> None: ...
    async def is_visible(self, selector: str, *, timeout_ms: int = 1000) -> bool: ...
    async def text(self, selector: str) -> str | None: ...
    async def query_all_attrs(self, selector: str, attr: str) -> list[str]: ...
    async def html(self) -> str: ...
    async def screenshot(self, path: str) -> None: ...
    async def evaluate(self, expression: str) -> Any: ...
    async def human_pause(self) -> None:
        """Random short pause to avoid bot fingerprints."""
        ...


@runtime_checkable
class BrowserDriver(Protocol):
    """Top-level browser session. Issues pages and persists storage state."""

    @asynccontextmanager
    def session(
        self,
        *,
        storage_state_path: str | None = None,
        headless: bool = True,
    ) -> AsyncIterator["BrowserSession"]:
        """Start a browser context. Cookies are persisted to
        `storage_state_path` on close so subsequent runs resume logged-in.
        """
        ...


@runtime_checkable
class BrowserSession(Protocol):
    """An open context. Yields pages."""

    @asynccontextmanager
    def page(self) -> AsyncIterator[BrowserPage]: ...

    async def save_storage_state(self) -> None: ...
