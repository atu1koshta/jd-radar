"""NaukriAdapter: search + fetch_jd flow against fully-mocked browser/auth/extractor."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from jobhunter.adapters.portals.naukri.adapter import NaukriAdapter
from jobhunter.core.entities import JobQuery
from jobhunter.ports.auth import Credentials, Session
from jobhunter.ports.portal import PortalContext


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePage:
    def __init__(self, *, urls: list[str], titles: list[str], companies: list[str]) -> None:
        self.url = "about:blank"
        self._urls = urls
        self._titles = titles
        self._companies = companies
        self._html = "<html><body>JD body</body></html>"
        self.gotos: list[str] = []
        self.visible: dict[str, bool] = {}

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.gotos.append(url)
        self.url = url

    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def type_text(self, selector: str, value: str, *, delay_ms: int = 25) -> None: ...
    async def wait_for(self, selector: str, *, timeout_ms: int = 10000) -> None: ...
    async def wait_for_url(self, pattern: str, *, timeout_ms: int = 10000) -> None: ...

    async def is_visible(self, selector: str, *, timeout_ms: int = 1000) -> bool:
        return self.visible.get(selector, False)

    async def text(self, selector: str) -> str | None:
        return None

    async def query_all_attrs(self, selector: str, attr: str) -> list[str]:
        return list(self._urls) if attr == "href" else []

    async def evaluate(self, expression: str) -> Any:
        # Distinguish title-link query from company-name query by the
        # selector substring the adapter embeds in the JS expression.
        if "jobTitle" in expression or "a.title" in expression:
            return list(self._titles)
        if "companyName" in expression or "subTitle" in expression:
            return list(self._companies)
        return []

    async def html(self) -> str:
        return self._html

    async def screenshot(self, path: str) -> None: ...
    async def human_pause(self) -> None: ...


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.saves = 0

    @asynccontextmanager
    async def page(self):
        yield self._page

    async def save_storage_state(self) -> None:
        self.saves += 1


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._session = FakeSession(page)
        self.session_opens = 0

    @asynccontextmanager
    async def session(self, *, storage_state_path=None, headless=True):
        self.session_opens += 1
        yield self._session


class FakeAuth:
    name = "fake-password"

    def __init__(self, *, already_authed: bool = True) -> None:
        self._authed = already_authed
        self.login_calls = 0

    async def is_authenticated(self, page) -> bool:
        return self._authed

    async def login(self, page, credentials):
        self.login_calls += 1
        self._authed = True
        return Session(portal="naukri", authenticated=True)


class FakeExtractor:
    def to_markdown(self, html: str) -> str:
        return f"MD::{html}"

    def main_text(self, html: str) -> str:
        return html


def _ctx(page: FakePage, *, already_authed: bool = True) -> tuple[PortalContext, FakeBrowser, FakeAuth]:
    browser = FakeBrowser(page)
    auth = FakeAuth(already_authed=already_authed)
    ctx = PortalContext(
        browser=browser,  # type: ignore[arg-type]
        auth=auth,  # type: ignore[arg-type]
        extractor=FakeExtractor(),  # type: ignore[arg-type]
        credentials=Credentials(email="x@y.z", password="pw"),
        storage_state_path="/tmp/naukri.json",
        headless=True,
    )
    return ctx, browser, auth


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_yields_jobs_built_from_dom_and_skips_login_when_authed() -> None:
    page = FakePage(
        urls=[
            "https://www.naukri.com/job-listings-senior-python-engineer-acme-12345",
            "https://www.naukri.com/job-listings-backend-eng-globex-67890",
        ],
        titles=["Senior Python Engineer", "Backend Engineer"],
        companies=["Acme", "Globex"],
    )
    ctx, _, auth = _ctx(page, already_authed=True)
    adapter = NaukriAdapter(ctx)

    results = []
    async for job in adapter.search(JobQuery(keywords="software engineer"), limit=5):
        results.append(job)
    await adapter.close()

    assert auth.login_calls == 0
    assert len(results) == 2
    assert results[0].external_id == "12345"
    assert results[0].company == "Acme"
    assert results[0].portal == "naukri"
    assert results[1].external_id == "67890"


@pytest.mark.asyncio
async def test_search_logs_in_when_session_not_authenticated() -> None:
    page = FakePage(
        urls=["https://www.naukri.com/job-listings-x-y-1"],
        titles=["X"],
        companies=["Y"],
    )
    ctx, _, auth = _ctx(page, already_authed=False)
    adapter = NaukriAdapter(ctx)

    results = [j async for j in adapter.search(JobQuery(keywords="software engineer"), limit=5)]
    await adapter.close()

    assert auth.login_calls == 1
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_respects_limit() -> None:
    page = FakePage(
        urls=[f"https://www.naukri.com/x-{i}" for i in range(20)],
        titles=[f"T{i}" for i in range(20)],
        companies=[f"C{i}" for i in range(20)],
    )
    ctx, _, _ = _ctx(page)
    adapter = NaukriAdapter(ctx)

    results = [j async for j in adapter.search(JobQuery(keywords="x"), limit=3)]
    await adapter.close()
    assert len(results) == 3


@pytest.mark.asyncio
async def test_fetch_jd_navigates_and_populates_jd_body_and_hash() -> None:
    page = FakePage(urls=[], titles=[], companies=[])
    page._html = "<html><body><div class='jd-desc'>FastAPI + Postgres role</div></body></html>"
    ctx, _, _ = _ctx(page)
    adapter = NaukriAdapter(ctx)

    seed_iter = adapter.search(JobQuery(keywords="x"), limit=0)  # type: ignore[var-annotated]
    # Build a Job manually since search returns no items
    from pydantic import HttpUrl
    from jobhunter.core.entities import Job
    job = Job(
        id="naukri:1",
        portal="naukri",
        external_id="1",
        url=HttpUrl("https://www.naukri.com/job-listings-x-1"),
        title="X",
        company="Y",
    )

    enriched = await adapter.fetch_jd(job)
    await adapter.close()

    assert enriched.jd_raw is not None
    assert enriched.jd_raw.startswith("MD::")
    assert "FastAPI" in enriched.jd_raw
    assert enriched.jd_content_hash != ""
    # Discard the unused seed_iter to keep linters happy.
    aclose = getattr(seed_iter, "aclose", None)
    if aclose is not None:
        await aclose()
