"""AuthStrategy port — portal login contract.

Strategies are stateless objects parameterised by selectors; they receive a
`BrowserPage` and `Credentials`, drive the login flow, and verify success.
The portal adapter chooses which strategy to instantiate; the strategy
never knows which portal it serves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from jobhunter.ports.browser import BrowserPage


class Credentials(BaseModel):
    """Vendor-neutral credential bag. Strategies pick the fields they need."""

    email: str | None = None
    password: str | None = None
    username: str | None = None
    totp_secret: str | None = None


class Session(BaseModel):
    portal: str
    authenticated: bool = False
    last_verified_at: datetime = Field(default_factory=datetime.utcnow)


@runtime_checkable
class AuthStrategy(Protocol):
    name: str

    async def is_authenticated(self, page: BrowserPage) -> bool:
        """Cheap probe — used to skip login when storage_state already
        carries a valid session."""
        ...

    async def login(
        self,
        page: BrowserPage,
        credentials: Credentials,
    ) -> Session:
        """Drive the login flow. Raise `AuthError` on captcha / wrong creds /
        unexpected DOM rather than returning a half-authenticated session.
        """
        ...
