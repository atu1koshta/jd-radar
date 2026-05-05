"""CaptchaSolver port.

v1 ships only the `manual_halt` adapter — when a captcha is spotted the
pipeline halts and a Telegram alert (Phase 3) is fired. The Protocol still
allows future plugins (2captcha, AntiCaptcha) without core changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from jobhunter.ports.browser import BrowserPage


class CaptchaOutcome(StrEnum):
    NOT_PRESENT = "not_present"
    SOLVED = "solved"
    HALTED = "halted"


@runtime_checkable
class CaptchaSolver(Protocol):
    name: str

    async def detect(self, page: BrowserPage) -> bool: ...

    async def solve(self, page: BrowserPage) -> CaptchaOutcome:
        """Attempt to clear the challenge. v1 implementations may simply
        raise `AuthError` after notifying the operator; future plugins can
        actually solve."""
        ...
