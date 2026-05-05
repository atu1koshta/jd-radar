"""Domain-level exceptions. Pure: no third-party imports."""

from __future__ import annotations


class JobHunterError(Exception):
    """Base for every domain error in jobhunter.core."""


class ConfigError(JobHunterError):
    """Configuration is missing or invalid."""


class ResumeError(JobHunterError):
    """Resume could not be loaded, parsed, or validated."""


class PortalError(JobHunterError):
    """Portal-side failure (HTTP, selector, anti-bot, ...)."""


class AuthError(PortalError):
    """Authentication / OTP / captcha failure."""


class ScoringError(JobHunterError):
    """Confidence/risk computation failed."""


class ActionError(JobHunterError):
    """An Action raised during execution. Wraps the original cause."""

    def __init__(self, action_name: str, message: str, cause: Exception | None = None):
        super().__init__(f"action={action_name}: {message}")
        self.action_name = action_name
        self.cause = cause


class LLMError(JobHunterError):
    """LLM call failed (network, timeout, schema-parse, capability mismatch)."""


class CapabilityError(LLMError):
    """The current LLM backend does not support the requested capability."""
