"""Job + JobQuery domain entities. Pure Pydantic, no IO."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


def jd_content_hash(jd_raw: str | None) -> str:
    """Stable sha256 of the raw JD body. Empty / missing → empty string.

    Used as the cache-invalidation key for any future per-JD artifact
    (LLM extraction, embedding) so re-fetched but unchanged JDs reuse
    everything previously computed for them.
    """
    if not jd_raw:
        return ""
    return hashlib.sha256(jd_raw.encode("utf-8")).hexdigest()


class JobQuery(BaseModel):
    """A search request handed to a portal."""

    model_config = ConfigDict(frozen=True)

    keywords: str
    location: str | None = None
    remote: bool | None = None
    min_experience_years: int | None = None
    max_experience_years: int | None = None
    salary_min: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    """A job posting collected from a portal.

    `jd_parsed` is filled in after the LLM extraction step; it stays None
    while the job lives only in the search-results queue.

    `jd_content_hash` lets later phases (embeddings, JD-extraction cache,
    cross-run dedup) skip work whenever a portal returns the same posting
    again with byte-identical body text.
    """

    id: str = Field(description="Stable internal id (e.g. uuid or portal_external)")
    portal: str
    external_id: str
    url: HttpUrl
    title: str
    company: str
    location: str | None = None
    jd_raw: str | None = None
    jd_content_hash: str = ""
    jd_parsed: dict[str, Any] | None = None
    embedding_ref: str | None = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
