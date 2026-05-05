"""Job + JobQuery domain entities. Pure Pydantic, no IO."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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
    """

    id: str = Field(description="Stable internal id (e.g. uuid or portal_external)")
    portal: str
    external_id: str
    url: HttpUrl
    title: str
    company: str
    location: str | None = None
    jd_raw: str | None = None
    jd_parsed: dict[str, Any] | None = None
    embedding_ref: str | None = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
