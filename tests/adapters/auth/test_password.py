"""PasswordAuth: login flow against a fully-mocked BrowserPage."""

from __future__ import annotations

from typing import Any

import pytest

from jobhunter.adapters.auth.password import PasswordAuth, PasswordAuthSelectors
from jobhunter.core.errors import AuthError
from jobhunter.ports.auth import Credentials


_SEL = PasswordAuthSelectors(
    login_url="https://example.test/login",
    email_input="#email",
    password_input="#password",
    submit_button="button[type='submit']",
    success_url_pattern="**/home/**",
    success_marker=".logged-in",
    error_marker=".error",
    captcha_marker=".captcha",
)


class FakePage:
    """Tiny `BrowserPage` stub. Records every action; predicates are
    individually overridable per test."""

    def __init__(self) -> None:
        self.url = "https://example.test/start"
        self.actions: list[tuple[str, Any]] = []
        self.visible: dict[str, bool] = {}
        self.wait_for_url_should_fail = False

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.actions.append(("goto", url))
        self.url = url

    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None:
        self.actions.append(("click", selector))

    async def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", (selector, value)))

    async def type_text(self, selector: str, value: str, *, delay_ms: int = 25) -> None:
        self.actions.append(("type", (selector, value)))

    async def wait_for(self, selector: str, *, timeout_ms: int = 10000) -> None:
        self.actions.append(("wait_for", selector))

    async def wait_for_url(self, pattern: str, *, timeout_ms: int = 10000) -> None:
        self.actions.append(("wait_for_url", pattern))
        if self.wait_for_url_should_fail:
            raise TimeoutError("URL never matched")

    async def is_visible(self, selector: str, *, timeout_ms: int = 1000) -> bool:
        return self.visible.get(selector, False)

    async def text(self, selector: str) -> str | None:
        return None

    async def query_all_attrs(self, selector: str, attr: str) -> list[str]:
        return []

    async def html(self) -> str:
        return ""

    async def screenshot(self, path: str) -> None:
        return None

    async def evaluate(self, expression: str) -> Any:
        return None

    async def human_pause(self) -> None:
        self.actions.append(("pause", None))


def _auth() -> PasswordAuth:
    return PasswordAuth(portal="naukri", selectors=_SEL, verify_timeout_ms=1000)


@pytest.mark.asyncio
async def test_login_happy_path_fills_form_and_returns_authenticated_session() -> None:
    page = FakePage()
    auth = _auth()
    creds = Credentials(email="user@example.com", password="hunter2")

    session = await auth.login(page, creds)

    assert session.authenticated is True
    assert session.portal == "naukri"
    # Verify the navigation + form-fill sequence.
    kinds = [a[0] for a in page.actions]
    assert kinds[0] == "goto"
    assert page.actions[0][1] == _SEL.login_url
    assert ("fill", ("#email", "user@example.com")) in page.actions
    assert ("fill", ("#password", "hunter2")) in page.actions
    assert ("click", "button[type='submit']") in page.actions
    assert ("wait_for_url", "**/home/**") in page.actions


@pytest.mark.asyncio
async def test_missing_credentials_raise_auth_error() -> None:
    page = FakePage()
    auth = _auth()
    with pytest.raises(AuthError):
        await auth.login(page, Credentials(email=None, password="x"))
    with pytest.raises(AuthError):
        await auth.login(page, Credentials(email="x", password=None))


@pytest.mark.asyncio
async def test_captcha_marker_visible_aborts_before_submit() -> None:
    page = FakePage()
    page.visible[".captcha"] = True
    auth = _auth()
    with pytest.raises(AuthError) as ei:
        await auth.login(page, Credentials(email="x", password="y"))
    assert "captcha" in str(ei.value).lower()
    # Submit click never fired.
    assert ("click", "button[type='submit']") not in page.actions


@pytest.mark.asyncio
async def test_success_url_timeout_with_error_marker_visible_reports_credential_error() -> None:
    page = FakePage()
    page.wait_for_url_should_fail = True
    page.visible[".error"] = True
    auth = _auth()
    with pytest.raises(AuthError) as ei:
        await auth.login(page, Credentials(email="x", password="bad"))
    assert "credentials" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_is_authenticated_uses_success_marker() -> None:
    page = FakePage()
    auth = _auth()
    assert await auth.is_authenticated(page) is False
    page.visible[".logged-in"] = True
    assert await auth.is_authenticated(page) is True
