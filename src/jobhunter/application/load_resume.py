"""Use case: ensure the active Resume is loaded and fresh.

The pipeline orchestrator calls this at the start of every run. It returns
the current Resume, refreshing from source automatically when the TTL has
elapsed.
"""

from __future__ import annotations

from jobhunter.core.entities import Resume
from jobhunter.ports.resume_loader import ResumeLoader


async def load_resume(loader: ResumeLoader, *, force_refresh: bool = False) -> Resume:
    return await loader.load(force_refresh=force_refresh)
