"""Generic password-based AuthStrategy.

Selector-driven so the same adapter works for Naukri, LinkedIn-classic, or
any portal that takes a `(email|username, password) -> submit` form. The
portal adapter passes the right selectors via constructor.

Verifies authentication by waiting for either a post-login URL pattern or
a logged-in DOM marker. If neither appears within `verify_timeout_ms`, we
raise `AuthError` rather than silently returning a half-authenticated
session.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from jobhunter.core.errors import AuthError
from jobhunter.ports.auth import Credentials, Session
from jobhunter.ports.browser import BrowserPage


@dataclass(frozen=True)
class PasswordAuthSelectors:
    login_url: str
    email_input: str
    password_input: str
    submit_button: str
    success_url_pattern: str | None = None
    success_marker: str | None = None
    error_marker: str | None = None
    captcha_marker: str | None = None
    # Substring that, if present in `page.url` after navigating to the
    # login form, means we were already authenticated and the portal
    # redirected us straight to the dashboard. Skips form fill in that
    # case. Example for Naukri: "/mnjuser/".
    authenticated_url_substring: str | None = None


class PasswordAuth:
    """Email + password login flow."""

    name = "password"

    def __init__(
        self,
        *,
        portal: str,
        selectors: PasswordAuthSelectors,
        verify_timeout_ms: int = 15000,
    ) -> None:
        self._portal = portal
        self._sel = selectors
        self._timeout = verify_timeout_ms

    async def is_authenticated(self, page: BrowserPage) -> bool:
        # URL check first — cheaper than DOM probe and survives marker drift.
        if self._sel.authenticated_url_substring and (
            self._sel.authenticated_url_substring in page.url
        ):
            return True
        if self._sel.success_marker:
            return await page.is_visible(self._sel.success_marker, timeout_ms=1500)
        return False

    async def login(
        self,
        page: BrowserPage,
        credentials: Credentials,
    ) -> Session:
        if not credentials.email or not credentials.password:
            raise AuthError(
                f"{self._portal}: email + password are both required for PasswordAuth"
            )

        await page.goto(self._sel.login_url)
        await page.human_pause()

        # Many portals do a JS-driven redirect from the login URL to the
        # dashboard when valid cookies are present. The redirect fires
        # *after* DOMContentLoaded, so a `page.url` snapshot taken
        # immediately would still show the login URL even though the form
        # will never render. Race the email-input selector against a
        # post-login URL substring: whichever wins decides the path.
        try:
            await page.wait_for(self._sel.email_input, timeout_ms=8000)
        except Exception:
            if self._sel.authenticated_url_substring and (
                self._sel.authenticated_url_substring in page.url
            ):
                logger.debug(
                    "{}: login URL redirected to {} — cookies still valid, skipping form fill",
                    self._portal,
                    page.url,
                )
                return Session(portal=self._portal, authenticated=True)
            raise

        # Final URL check — a slow JS redirect could still have fired
        # while we were waiting for the input element.
        if self._sel.authenticated_url_substring and (
            self._sel.authenticated_url_substring in page.url
        ):
            return Session(portal=self._portal, authenticated=True)

        await page.fill(self._sel.email_input, credentials.email)
        await page.human_pause()

        await page.fill(self._sel.password_input, credentials.password)
        await page.human_pause()

        # Captcha must be checked BEFORE clicking submit; some portals
        # render the challenge inside the form once the email is typed.
        if self._sel.captcha_marker and await page.is_visible(
            self._sel.captcha_marker, timeout_ms=1000
        ):
            raise AuthError(
                f"{self._portal}: captcha detected before submit; halting"
            )

        await page.click(self._sel.submit_button)

        # Verify success.
        if self._sel.success_url_pattern:
            try:
                await page.wait_for_url(
                    self._sel.success_url_pattern, timeout_ms=self._timeout
                )
            except Exception as e:  # noqa: BLE001
                if self._sel.error_marker and await page.is_visible(
                    self._sel.error_marker, timeout_ms=1000
                ):
                    raise AuthError(
                        f"{self._portal}: login form returned an error; check credentials"
                    ) from e
                raise AuthError(
                    f"{self._portal}: did not reach {self._sel.success_url_pattern} "
                    f"within {self._timeout}ms"
                ) from e
        elif self._sel.success_marker:
            try:
                await page.wait_for(
                    self._sel.success_marker, timeout_ms=self._timeout
                )
            except Exception as e:  # noqa: BLE001
                raise AuthError(
                    f"{self._portal}: login marker '{self._sel.success_marker}' never appeared"
                ) from e
        else:
            logger.warning(
                "{}: no success_url_pattern / success_marker configured; "
                "assuming login succeeded after submit",
                self._portal,
            )

        return Session(portal=self._portal, authenticated=True)
