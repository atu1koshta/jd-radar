"""GitHubYamlResumeLoader: TTL gate, ETag refresh, hash-based cache, interpreter wiring."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from jobhunter.adapters.resume_loader.github_yaml import (
    RESUME_ID,
    GitHubYamlResumeLoader,
)
from jobhunter.core.entities import (
    InterpretedResume,
    Resume,
    canonical_body_hash,
)
from jobhunter.core.errors import ResumeError

YAML_V1 = """\
name: Atul
headline: Software architect
skills:
  - Python
  - FastAPI
"""

YAML_V2 = """\
name: Atul Koshta
headline: Software architect & AI engineer
skills:
  - Python
  - FastAPI
certifications:
  - aws-saa
"""


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


class FakeResumeRepo:
    def __init__(self) -> None:
        self._row: Resume | None = None

    async def get(self, id: str) -> Resume | None:
        if self._row is None or self._row.id != id:
            return None
        return self._row

    async def list(self, **filter: Any) -> list[Resume]:
        return [self._row] if self._row else []

    async def save(self, entity: Resume) -> Resume:
        self._row = entity
        return entity

    async def delete(self, id: str) -> None:
        if self._row and self._row.id == id:
            self._row = None


class CountingInterpreter:
    """Records every call and returns a deterministic InterpretedResume."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def interpret(
        self, *, body: dict[str, Any], body_hash: str
    ) -> InterpretedResume:
        self.calls.append(body_hash)
        return InterpretedResume(
            canonical_name=str(body.get("name") or ""),
            headline=body.get("headline"),
            summary="auto-generated summary",
            total_experience_years=5.0,
            seniority_level="senior",
            body_hash=body_hash,
            model_used="fake-llm",
        )


def _loader(
    *,
    repo: FakeResumeRepo,
    interpreter: CountingInterpreter,
    transport: httpx.MockTransport,
    cache_path: Path,
    ttl_minutes: int = 60,
) -> GitHubYamlResumeLoader:
    client = httpx.AsyncClient(transport=transport, base_url="https://example.test")
    return GitHubYamlResumeLoader(
        url="https://example.test/resume.yaml",
        repo=repo,  # type: ignore[arg-type]
        cache_path=cache_path,
        refresh_ttl_minutes=ttl_minutes,
        interpreter=interpreter,  # type: ignore[arg-type]
        client=client,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_load_fetches_interprets_and_persists(tmp_path: Path) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=YAML_V1, headers={"ETag": '"v1"'})

    cache = tmp_path / "resume_cache.yaml"
    loader = _loader(
        repo=repo, interpreter=interp, transport=httpx.MockTransport(handler), cache_path=cache
    )

    resume = await loader.load()

    assert resume.body["name"] == "Atul"
    assert resume.etag == '"v1"'
    assert resume.body_hash == canonical_body_hash(resume.body)
    assert resume.interpreted is not None
    assert resume.interpreted.canonical_name == "Atul"
    assert len(interp.calls) == 1

    persisted = await repo.get(RESUME_ID)
    assert persisted is not None
    assert persisted.interpreted is not None

    assert cache.exists() and "Atul" in cache.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_within_ttl_returns_cache_without_http_or_interpret(tmp_path: Path) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not hit the network when within TTL")

    loader = _loader(
        repo=repo,
        interpreter=interp,
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "c.yaml",
        ttl_minutes=60,
    )

    fresh = Resume(
        body={"name": "Cached"},
        body_hash=canonical_body_hash({"name": "Cached"}),
        etag='"vX"',
        last_updated_at=datetime.utcnow(),
        interpreted=InterpretedResume(
            summary="cached", body_hash=canonical_body_hash({"name": "Cached"})
        ),
    )
    await repo.save(fresh)

    out = await loader.load()
    assert out.body["name"] == "Cached"
    assert interp.calls == []  # cache hit; no re-interpret


@pytest.mark.asyncio
async def test_within_ttl_but_missing_interpretation_triggers_interpret(tmp_path: Path) -> None:
    """Edge case: a partially-persisted Resume from an old run."""
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not hit the network within TTL")

    loader = _loader(
        repo=repo,
        interpreter=interp,
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "c.yaml",
        ttl_minutes=60,
    )

    half_done = Resume(
        body={"name": "HalfDone"},
        body_hash=canonical_body_hash({"name": "HalfDone"}),
        last_updated_at=datetime.utcnow(),
        interpreted=None,
    )
    await repo.save(half_done)

    out = await loader.load()
    assert out.interpreted is not None
    assert len(interp.calls) == 1


@pytest.mark.asyncio
async def test_expired_with_304_keeps_interpretation_and_bumps_timestamp(
    tmp_path: Path,
) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    loader = _loader(
        repo=repo,
        interpreter=interp,
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "c.yaml",
        ttl_minutes=60,
    )

    stale_ts = datetime.utcnow() - timedelta(hours=2)
    body = {"name": "Stale"}
    stale = Resume(
        body=body,
        body_hash=canonical_body_hash(body),
        etag='"v1"',
        last_updated_at=stale_ts,
        interpreted=InterpretedResume(
            summary="cached", body_hash=canonical_body_hash(body)
        ),
    )
    await repo.save(stale)

    out = await loader.load()
    assert out.last_updated_at > stale_ts
    assert out.interpreted is not None
    assert interp.calls == []


@pytest.mark.asyncio
async def test_expired_with_200_same_body_keeps_interpretation(tmp_path: Path) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=YAML_V1, headers={"ETag": '"v1-fresh"'})

    loader = _loader(
        repo=repo,
        interpreter=interp,
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "c.yaml",
        ttl_minutes=60,
    )

    # Pre-populate cache with the same YAML body (hash will match the
    # incoming response) and an existing interpretation.
    body_dict = {"name": "Atul", "headline": "Software architect", "skills": ["Python", "FastAPI"]}
    h = canonical_body_hash(body_dict)
    stale = Resume(
        body=body_dict,
        body_hash=h,
        etag='"v1"',
        last_updated_at=datetime.utcnow() - timedelta(hours=2),
        interpreted=InterpretedResume(summary="cached", body_hash=h),
    )
    await repo.save(stale)

    out = await loader.load()
    assert out.body_hash == h
    assert out.interpreted is not None
    assert interp.calls == []  # body unchanged → reuse interpretation


@pytest.mark.asyncio
async def test_expired_with_200_changed_body_reinterprets(tmp_path: Path) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=YAML_V2, headers={"ETag": '"v2"'})

    loader = _loader(
        repo=repo,
        interpreter=interp,
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "c.yaml",
        ttl_minutes=60,
    )

    old_body = {"name": "Old"}
    stale = Resume(
        body=old_body,
        body_hash=canonical_body_hash(old_body),
        etag='"v1"',
        last_updated_at=datetime.utcnow() - timedelta(hours=2),
        interpreted=InterpretedResume(
            summary="old", body_hash=canonical_body_hash(old_body)
        ),
    )
    await repo.save(stale)

    out = await loader.load()
    assert out.body["name"] == "Atul Koshta"
    assert out.body["certifications"] == ["aws-saa"]
    assert len(interp.calls) == 1
    assert out.interpreted is not None
    assert out.interpreted.canonical_name == "Atul Koshta"


@pytest.mark.asyncio
async def test_force_refresh_bypasses_ttl_but_skips_interpret_when_body_unchanged(
    tmp_path: Path,
) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=YAML_V1, headers={"ETag": '"v1"'})

    loader = _loader(
        repo=repo,
        interpreter=interp,
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "c.yaml",
        ttl_minutes=60,
    )

    body_dict = {"name": "Atul", "headline": "Software architect", "skills": ["Python", "FastAPI"]}
    h = canonical_body_hash(body_dict)
    fresh = Resume(
        body=body_dict,
        body_hash=h,
        etag='"v1"',
        last_updated_at=datetime.utcnow(),
        interpreted=InterpretedResume(summary="cached", body_hash=h),
    )
    await repo.save(fresh)

    out = await loader.load(force_refresh=True)
    # HTTP was hit (force_refresh), but body was identical → no re-interpret.
    assert out.interpreted is not None
    assert interp.calls == []


@pytest.mark.asyncio
async def test_malformed_yaml_raises_resume_error(tmp_path: Path) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=":\n  - oops: [unterminated", headers={"ETag": '"x"'})

    loader = _loader(
        repo=repo, interpreter=interp, transport=httpx.MockTransport(handler), cache_path=tmp_path / "c.yaml"
    )
    with pytest.raises(ResumeError):
        await loader.load()


@pytest.mark.asyncio
async def test_network_error_with_existing_cache_serves_stale(tmp_path: Path) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    loader = _loader(
        repo=repo,
        interpreter=interp,
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "c.yaml",
        ttl_minutes=60,
    )

    body = {"name": "Stale"}
    stale_ts = datetime.utcnow() - timedelta(hours=5)
    stale = Resume(
        body=body,
        body_hash=canonical_body_hash(body),
        etag='"v1"',
        last_updated_at=stale_ts,
        interpreted=InterpretedResume(
            summary="cached", body_hash=canonical_body_hash(body)
        ),
    )
    await repo.save(stale)

    out = await loader.load()
    assert out.body["name"] == "Stale"
    assert out.last_updated_at == stale_ts


@pytest.mark.asyncio
async def test_network_error_without_cache_raises(tmp_path: Path) -> None:
    repo = FakeResumeRepo()
    interp = CountingInterpreter()

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    loader = _loader(
        repo=repo, interpreter=interp, transport=httpx.MockTransport(handler), cache_path=tmp_path / "c.yaml"
    )
    with pytest.raises(ResumeError):
        await loader.load()
