"""PortalAdapter port — search + fetch_jd contract.

v1 explicitly excludes `apply()`; adding new actions (apply, save, follow)
happens through the `Action` plugin subsystem (Phase 3) rather than by
growing this Protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from jobhunter.core.entities import Job, JobQuery
from jobhunter.ports.auth import AuthStrategy, Credentials
from jobhunter.ports.browser import BrowserDriver
from jobhunter.ports.page_extractor import PageExtractor


@dataclass
class PortalContext:
    """Bundle of dependencies a portal adapter needs to run.

    Built by the composition root; the portal adapter never reaches out to
    pick its own deps.
    """

    browser: BrowserDriver
    auth: AuthStrategy
    extractor: PageExtractor
    credentials: Credentials
    storage_state_path: str
    headless: bool = True
    rate_limit_per_min: int = 10
    daily_search_cap: int = 200


@runtime_checkable
class PortalAdapter(Protocol):
    name: str

    async def search(
        self,
        query: JobQuery,
        *,
        limit: int = 10,
    ) -> AsyncIterator[Job]:
        """Yield matching jobs. Adapters MUST short-circuit at `limit` to
        respect daily caps."""
        ...

    async def fetch_jd(self, job: Job) -> Job:
        """Populate `jd_raw` (and `jd_content_hash`) on the given job by
        navigating to its URL and extracting the main text."""
        ...

    async def health_check(self) -> bool:
        """Cheap sanity probe: can we reach the portal at all?"""
        ...

    async def close(self) -> None:
        """Release any browser sessions / cookies the adapter holds open."""
        ...
