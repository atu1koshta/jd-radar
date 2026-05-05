"""ResumeLoader backed by a raw YAML URL on GitHub.

Refresh policy
--------------
- Persisted copy lives in `Repository[Resume]` (id=`resume:current`).
- TTL gate: `now() - resume.last_updated_at >= ttl` triggers a refresh.
- HTTP refresh sends `If-None-Match: <stored_etag>`.
  - 304 Not Modified → bump `last_updated_at` only (interpretation reused).
  - 200 OK → parse + recompute body_hash. If hash matches the cached one,
    reuse the cached interpretation; if it differs, drop it and re-interpret.
- `force_refresh=True` bypasses the TTL gate but still respects the hash
  comparison, so a no-op refresh is still cheap.

Schema drift handling
---------------------
The full YAML is preserved in `Resume.body`. Whatever the user writes in
their resume.yaml — known sections or brand-new ones — flows through
untouched. Canonical typed access happens via `Resume.interpreted`,
populated lazily by `ResumeInterpreter`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml
from loguru import logger
from pydantic import ValidationError

from jobhunter.core.entities import Resume, canonical_body_hash
from jobhunter.core.errors import ResumeError
from jobhunter.ports.repository import Repository
from jobhunter.ports.resume_interpreter import ResumeInterpreter

RESUME_ID = "resume:current"


class GitHubYamlResumeLoader:
    """Fetch + cache + interpret + persist the user's resume.yaml from GitHub."""

    def __init__(
        self,
        *,
        url: str,
        repo: Repository[Resume],
        cache_path: Path,
        refresh_ttl_minutes: int,
        interpreter: ResumeInterpreter,
        github_token: str | None = None,
        request_timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.repo = repo
        self.cache_path = cache_path
        self.ttl = timedelta(minutes=max(0, refresh_ttl_minutes))
        self._interpreter = interpreter
        self._token = github_token
        self._timeout = request_timeout
        self._client = client

    # ---- public API ----------------------------------------------------

    async def load(self, *, force_refresh: bool = False) -> Resume:
        existing = await self.repo.get(RESUME_ID)

        # Legacy / corrupt rows: persisted before the schema migration, or
        # an interrupted run before the body landed. Treat as no-cache and
        # do an unconditional fetch (no `If-None-Match`).
        if existing is not None and (not existing.body or not existing.body_hash):
            logger.info(
                "resume cache row is empty / pre-migration; discarding and refetching"
            )
            await self.repo.delete(RESUME_ID)
            existing = None

        if not force_refresh and existing and self._fresh(existing):
            logger.debug(
                "resume cache hit (age={}s, ttl={}s, has_interpreted={})",
                int(self._age(existing).total_seconds()),
                int(self.ttl.total_seconds()),
                existing.interpreted is not None,
            )
            # Defensive: if interpretation was lost but body is present,
            # interpret now.
            if existing.interpreted is None:
                return await self._interpret_and_persist(existing)
            return existing

        logger.info(
            "resume refresh: force={}, has_cache={}, url={}",
            force_refresh,
            existing is not None,
            self.url,
        )
        return await self._refresh(existing)

    # ---- internals -----------------------------------------------------

    def _age(self, resume: Resume) -> timedelta:
        return datetime.utcnow() - resume.last_updated_at

    def _fresh(self, resume: Resume) -> bool:
        return self._age(resume) < self.ttl if self.ttl.total_seconds() > 0 else False

    def _headers(self, etag: str | None) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "text/plain, */*"}
        if etag:
            h["If-None-Match"] = etag
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _get(self, etag: str | None) -> httpx.Response:
        async def _call(c: httpx.AsyncClient) -> httpx.Response:
            return await c.get(self.url, headers=self._headers(etag))

        if self._client is not None:
            return await _call(self._client)
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            return await _call(c)

    async def _refresh(self, existing: Resume | None) -> Resume:
        try:
            resp = await self._get(existing.etag if existing else None)
        except httpx.HTTPError as e:
            if existing is not None:
                logger.warning("resume fetch failed ({}); serving stale cache", e)
                return existing
            raise ResumeError(f"resume fetch failed and no cache available: {e}") from e

        if resp.status_code == 304 and existing is not None:
            bumped = existing.model_copy(update={"last_updated_at": datetime.utcnow()})
            await self.repo.save(bumped)
            return bumped

        if resp.status_code != 200:
            if existing is not None:
                logger.warning(
                    "resume fetch returned {}; serving stale cache",
                    resp.status_code,
                )
                return existing
            raise ResumeError(
                f"resume fetch returned HTTP {resp.status_code} and no cache exists"
            )

        body = self._parse(resp.text)
        new_hash = canonical_body_hash(body)
        new_etag = resp.headers.get("ETag")

        # Body identical to cached → keep interpretation; just freshen metadata.
        if existing and existing.body_hash == new_hash and existing.interpreted:
            refreshed = existing.model_copy(
                update={
                    "last_updated_at": datetime.utcnow(),
                    "etag": new_etag or existing.etag,
                    "source_url": self.url,
                }
            )
            await self._write_cache(resp.text)
            await self.repo.save(refreshed)
            return refreshed

        # Body changed (or no prior interpretation) → re-interpret.
        candidate = Resume(
            body=body,
            body_hash=new_hash,
            etag=new_etag,
            source_url=self.url,
            last_updated_at=datetime.utcnow(),
        )
        await self._write_cache(resp.text)
        return await self._interpret_and_persist(candidate)

    def _parse(self, text: str) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise ResumeError(f"resume YAML parse failed: {e}") from e
        if not isinstance(raw, dict):
            raise ResumeError(
                f"resume YAML must be a mapping at the top level, got {type(raw).__name__}"
            )
        return raw

    async def _interpret_and_persist(self, resume: Resume) -> Resume:
        try:
            interpreted = await self._interpreter.interpret(
                body=resume.body, body_hash=resume.body_hash
            )
        except ResumeError:
            # Interpretation failed but we have the body. Persist without
            # an interpretation so the pipeline can still consult the raw
            # body until the next refresh succeeds.
            logger.warning(
                "resume interpretation failed; persisting body without interpreted view"
            )
            await self.repo.save(resume)
            raise

        with_interpreted = resume.model_copy(update={"interpreted": interpreted})
        await self.repo.save(with_interpreted)
        return with_interpreted

    async def _write_cache(self, body_text: str) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(body_text, encoding="utf-8")
        except OSError as e:
            logger.warning("could not write resume cache file ({}): {}", self.cache_path, e)


# Backwards-compat alias for tests + downstream code that still references the
# old constructor signature.
__all__ = ["GitHubYamlResumeLoader", "RESUME_ID"]


def _legacy_validation_error_passthrough(exc: ValidationError) -> ResumeError:  # pragma: no cover
    return ResumeError(f"resume validation failed: {exc}")
