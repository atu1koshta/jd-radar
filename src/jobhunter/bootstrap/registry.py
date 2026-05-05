"""Plugin discovery via Python entry points.

Built-in adapters register themselves in `pyproject.toml`:

    [project.entry-points."jobhunter.llm"]
    ollama = "jobhunter.adapters.llm.ollama:OllamaProvider"

Third-party packages drop in additional portals / actions / notifiers / LLM
backends without touching this codebase.

Failures are logged loudly (`loguru.warning`) but never raised — a single
broken plugin must not take down the rest of the registry.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any

from loguru import logger

GROUP_LLM = "jobhunter.llm"
GROUP_PORTALS = "jobhunter.portals"
GROUP_ACTIONS = "jobhunter.actions"
GROUP_NOTIFIERS = "jobhunter.notifiers"
GROUP_EMBEDDINGS = "jobhunter.embeddings"
GROUP_EMAIL_SENDERS = "jobhunter.email_senders"


def _entry_points(group: str) -> dict[str, type[Any]]:
    """Return {name: class} for everything registered under `group`.

    Each load failure is logged with the entry-point name + reason so plugin
    authors can debug "why isn't my adapter showing up" without spelunking.
    """
    found: dict[str, type[Any]] = {}
    eps = metadata.entry_points()
    selection = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])
    for ep in selection:
        try:
            found[ep.name] = ep.load()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "entry_point '{}.{}' failed to load ({}: {}); skipping",
                group,
                ep.name,
                type(e).__name__,
                e,
            )
    return found


def llm_providers() -> dict[str, type[Any]]:
    return _entry_points(GROUP_LLM)


def portal_adapters() -> dict[str, type[Any]]:
    return _entry_points(GROUP_PORTALS)


def actions() -> dict[str, type[Any]]:
    return _entry_points(GROUP_ACTIONS)


def notifiers() -> dict[str, type[Any]]:
    return _entry_points(GROUP_NOTIFIERS)


def embedding_providers() -> dict[str, type[Any]]:
    return _entry_points(GROUP_EMBEDDINGS)


def email_senders() -> dict[str, type[Any]]:
    return _entry_points(GROUP_EMAIL_SENDERS)


def all_groups() -> dict[str, dict[str, type[Any]]]:
    """Snapshot every registered plugin group at once. Used by Container."""
    return {
        "llm": llm_providers(),
        "portals": portal_adapters(),
        "actions": actions(),
        "notifiers": notifiers(),
        "embeddings": embedding_providers(),
        "email_senders": email_senders(),
    }
