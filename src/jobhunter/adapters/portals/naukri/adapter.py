"""Naukri PortalAdapter — search + fetch_jd via password login.

Selectors live in `selectors.yaml` next to this module. The adapter is
deliberately small; the heavy lifting (browser, page extraction, auth) is
delegated to the injected dependencies on `PortalContext`.

v1 does NOT implement apply(). All actions on a job (alert / draft / future
auto-apply) flow through the `Action` plugin subsystem (Phase 3).
"""

from __future__ import annotations

import urllib.parse
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import HttpUrl

from jobhunter.adapters.auth.password import PasswordAuth, PasswordAuthSelectors
from jobhunter.adapters.captcha.manual_halt import ManualHaltCaptchaSolver
from jobhunter.core.entities import Job, JobQuery, jd_content_hash
from jobhunter.core.errors import PortalError
from jobhunter.ports.browser import BrowserPage
from jobhunter.ports.portal import PortalContext

_SELECTORS_PATH = Path(__file__).parent / "selectors.yaml"

# Naukri's `ctcFilter` accepts only these discrete (lower, upper) bands
# (lakhs/annum). An arbitrary expected CTC is floor-snapped onto whichever
# band starts at or below it. The top band is "1-5 Cr" in the UI; encoded
# here as 100-500 lakhs to keep one consistent unit.
_CTC_BANDS: tuple[tuple[int, int], ...] = (
    (0, 3), (3, 6), (6, 10), (10, 15),
    (15, 25), (25, 50), (50, 75), (75, 100), (100, 500),
)


def _load_selectors() -> dict[str, Any]:
    return yaml.safe_load(_SELECTORS_PATH.read_text(encoding="utf-8"))


def _ctc_band_for(expected_lpa: int) -> str:
    """Floor-snap `expected_lpa` to a Naukri ctcFilter band (`<min>to<max>`).

    `expected=25` -> "25to50". `expected=30` -> "25to50" (floor). Negative
    inputs clamp to the lowest band.
    """
    for low, high in reversed(_CTC_BANDS):
        if expected_lpa >= low:
            return f"{low}to{high}"
    return f"{_CTC_BANDS[0][0]}to{_CTC_BANDS[0][1]}"


def password_auth_for_naukri() -> PasswordAuth:
    """Factory that hands the portal-test wiring a ready-to-use
    `PasswordAuth` for Naukri, with selectors pulled from the yaml."""
    sel = _load_selectors()["login"]
    return PasswordAuth(
        portal="naukri",
        selectors=PasswordAuthSelectors(
            login_url=sel["url"],
            email_input=sel["email_input"],
            password_input=sel["password_input"],
            submit_button=sel["submit_button"],
            success_url_pattern=sel.get("success_url_pattern"),
            success_marker=sel.get("success_marker"),
            error_marker=sel.get("error_marker"),
            captcha_marker=sel.get("captcha_marker"),
            authenticated_url_substring=sel.get("authenticated_url_substring"),
        ),
    )


class NaukriAdapter:
    """Naukri portal — search jobs + fetch JD bodies. No apply in v1."""

    name = "naukri"

    def __init__(self, ctx: PortalContext) -> None:
        self._ctx = ctx
        self._selectors = _load_selectors()
        self._captcha = ManualHaltCaptchaSolver()
        self._stack: AsyncExitStack | None = None
        self._session = None  # type: ignore[var-annotated]

    # ---- lifecycle ----------------------------------------------------

    async def _ensure_session(self):  # type: ignore[no-untyped-def]
        if self._session is not None:
            return self._session
        self._stack = AsyncExitStack()
        self._session = await self._stack.enter_async_context(
            self._ctx.browser.session(
                storage_state_path=self._ctx.storage_state_path,
                headless=self._ctx.headless,
            )
        )
        return self._session

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None

    # ---- internals ----------------------------------------------------

    async def _logged_in_page(self) -> tuple[Any, BrowserPage]:
        """Open a page in the active session, ensuring auth.

        Returns (`page_cm`, `page`) — caller is responsible for awaiting
        `page_cm.__aexit__` once done. The pattern lets us reuse the same
        page across the search + fetch_jd loop without re-logging-in.

        We probe-navigate to the dashboard URL first: with valid cookies
        Naukri renders it; without, it bounces us to the login page. That
        gives `is_authenticated` a meaningful URL to inspect and avoids
        the cold-start "fresh page → login attempted → cookies redirect →
        form never appears" race.
        """
        session = await self._ensure_session()
        page_cm = session.page()
        page = await page_cm.__aenter__()  # type: ignore[union-attr]
        try:
            await page.goto("https://www.naukri.com/mnjuser/homepage")
            await page.human_pause()
            already = await self._ctx.auth.is_authenticated(page)
            if not already:
                logger.info("naukri: storage_state did not include a live session; logging in")
                await self._ctx.auth.login(page, self._ctx.credentials)
                # Persist cookies right after a fresh login so a crash mid-search
                # doesn't force another login on the next run.
                await session.save_storage_state()
            else:
                logger.debug("naukri: storage_state authenticated; skipped login")
        except Exception:
            await page_cm.__aexit__(None, None, None)  # type: ignore[union-attr]
            raise
        return page_cm, page

    def _search_url(self, query: JobQuery) -> str:
        tpl = self._selectors["search"]["url_template"]
        # Slug: lowercase, hyphenate, strip non-alphanum-hyphen chars. Naukri
        # search prefers `https://www.naukri.com/<slug>-jobs?k=...` over the
        # bare `?keyword=` form.
        slug_raw = query.keywords.strip().lower()
        slug = "-".join(slug_raw.split())
        slug = "".join(c if (c.isalnum() or c == "-") else "" for c in slug) or "jobs"
        base = tpl.format(
            slug=slug,
            keywords=urllib.parse.quote_plus(query.keywords),
            location=urllib.parse.quote_plus(query.location or ""),
        )
        return self._append_filter_params(base, query)

    def _append_filter_params(self, base_url: str, query: JobQuery) -> str:
        """Translate canonical `JobQuery` filters to Naukri URL params."""
        extra: list[tuple[str, str]] = []

        filter_map: dict[str, dict[str, str]] = (
            self._selectors["search"].get("filters") or {}
        )
        for field_name, spec in filter_map.items():
            value = getattr(query, field_name, None)
            if value is None:
                continue
            extra.append((spec["param"], spec["fmt"].format(value=value)))

        if query.expected_ctc_lpa is not None:
            extra.append(("ctcFilter", _ctc_band_for(query.expected_ctc_lpa)))

        if not extra:
            return base_url
        sep = "&" if "?" in base_url else "?"
        return base_url + sep + urllib.parse.urlencode(extra)

    async def _dump_debug(self, page: BrowserPage, label: str) -> None:
        """On a selector / nav failure, persist a screenshot + raw HTML so
        the operator can inspect what Naukri actually returned."""
        from datetime import datetime

        out_dir = Path("data/debug")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        png = out_dir / f"naukri-{label}-{ts}.png"
        html = out_dir / f"naukri-{label}-{ts}.html"
        try:
            await page.screenshot(str(png))
            html.write_text(await page.html(), encoding="utf-8")
            logger.warning("naukri debug snapshot: {} + {}", png, html)
        except Exception as e:  # noqa: BLE001
            logger.warning("naukri debug snapshot failed: {}", e)

    # ---- public API ---------------------------------------------------

    async def search(self, query: JobQuery, *, limit: int = 10) -> AsyncIterator[Job]:
        page_cm, page = await self._logged_in_page()
        try:
            await self._captcha.solve(page) if await self._captcha.detect(page) else None

            await page.goto(self._search_url(query))
            await page.human_pause()

            card_sel = self._selectors["search"]["job_card"]
            link_sel = self._selectors["search"]["job_title_link"]

            try:
                await page.wait_for(card_sel, timeout_ms=20000)
            except Exception as e:  # noqa: BLE001
                await self._dump_debug(page, "search-no-cards")
                raise PortalError(
                    f"naukri: search results never rendered ({type(e).__name__}) at {page.url}. "
                    f"DOM may have changed — see data/debug/naukri-search-no-cards-*.png/html "
                    f"and update src/jobhunter/adapters/portals/naukri/selectors.yaml."
                ) from e

            urls = await page.query_all_attrs(link_sel, "href")
            titles_raw = await page.evaluate(
                f"Array.from(document.querySelectorAll({link_sel!r})).map(e => e.innerText)"
            )
            companies_raw = await page.evaluate(
                f"Array.from(document.querySelectorAll("
                f"{self._selectors['search']['company_name']!r})).map(e => e.innerText)"
            )

            n = min(limit, len(urls))
            for i in range(n):
                url = urls[i]
                title = (titles_raw[i] if i < len(titles_raw) else "").strip()
                company = (companies_raw[i] if i < len(companies_raw) else "").strip()
                external_id = self._extract_external_id(url)
                yield Job(
                    id=f"naukri:{external_id}",
                    portal=self.name,
                    external_id=external_id,
                    url=HttpUrl(url),
                    title=title or "(unknown)",
                    company=company or "(unknown)",
                )
        finally:
            await page_cm.__aexit__(None, None, None)  # type: ignore[union-attr]

    async def fetch_jd(self, job: Job) -> Job:
        page_cm, page = await self._logged_in_page()
        try:
            await page.goto(str(job.url))
            await page.human_pause()

            if await self._captcha.detect(page):
                await self._captcha.solve(page)

            desc_sel = self._selectors["jd"]["description"]
            try:
                await page.wait_for(desc_sel, timeout_ms=15000)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "naukri: JD container '{}' not found on {}; falling back to full page",
                    desc_sel,
                    job.url,
                )

            html = await page.html()
            md = self._ctx.extractor.to_markdown(html)
            return job.model_copy(
                update={
                    "jd_raw": md,
                    "jd_content_hash": jd_content_hash(md),
                }
            )
        finally:
            await page_cm.__aexit__(None, None, None)  # type: ignore[union-attr]

    async def health_check(self) -> bool:
        try:
            session = await self._ensure_session()
            async with session.page() as page:
                await page.goto(self._ctx.credentials and "https://www.naukri.com" or "https://www.naukri.com")
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("naukri health_check failed: {}", e)
            return False

    @staticmethod
    def _extract_external_id(url: str) -> str:
        # Naukri job URLs typically end with `-<digits>` (the job id).
        tail = url.rstrip("/").split("-")[-1]
        if tail.isdigit():
            return tail
        # Fallback: hash the URL so we still get something stable.
        return f"u{uuid.uuid5(uuid.NAMESPACE_URL, url).hex[:12]}"
