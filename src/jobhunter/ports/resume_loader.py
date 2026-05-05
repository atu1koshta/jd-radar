"""ResumeLoader port.

The use-case layer never knows whether the resume came from GitHub, a local
file, or a Notion page. Adapters implement the contract; new sources drop
in without touching the pipeline.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobhunter.core.entities import Resume


@runtime_checkable
class ResumeLoader(Protocol):
    """Returns the active Resume, refreshing from source when stale."""

    async def load(self, *, force_refresh: bool = False) -> Resume:
        """Return the active Resume.

        Implementation contract:
        - If a cached / persisted copy exists AND is younger than the
          configured TTL AND `force_refresh` is False, return it as-is.
        - Otherwise fetch from the remote source. On 200, persist the new
          copy (DB + on-disk cache). On 304 Not Modified, bump the
          `last_updated_at` timestamp on the existing copy and return it.
        """
        ...
