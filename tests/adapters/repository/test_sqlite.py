"""SQLiteRepository round-trips arbitrary Pydantic entities."""

from __future__ import annotations

import pytest

from jobhunter.adapters.repository.sqlite import SQLiteRepository
from jobhunter.core.entities import ActionRecord, ActionStatus, Decision, Match


@pytest.mark.asyncio
async def test_save_get_roundtrip_preserves_entity(tmp_db_url: str) -> None:
    repo: SQLiteRepository[Match] = SQLiteRepository(Match, database_url=tmp_db_url)
    m = Match(
        id="m1",
        job_id="j1",
        confidence=0.7,
        risk=0.2,
        decision=Decision.ALERT,
        breakdown={"skill_overlap": 0.8},
    )
    await repo.save(m)

    fetched = await repo.get("m1")
    assert fetched is not None
    assert fetched.id == "m1"
    assert fetched.confidence == 0.7
    assert fetched.decision is Decision.ALERT
    assert fetched.breakdown == {"skill_overlap": 0.8}


@pytest.mark.asyncio
async def test_save_overwrites_existing_row(tmp_db_url: str) -> None:
    repo: SQLiteRepository[Match] = SQLiteRepository(Match, database_url=tmp_db_url)
    m = Match(id="m1", job_id="j1", confidence=0.4, risk=0.6, decision=Decision.SKIP)
    await repo.save(m)
    m2 = m.model_copy(update={"confidence": 0.9, "decision": Decision.DRAFT})
    await repo.save(m2)

    fetched = await repo.get("m1")
    assert fetched is not None
    assert fetched.confidence == 0.9
    assert fetched.decision is Decision.DRAFT


@pytest.mark.asyncio
async def test_list_filters_in_python(tmp_db_url: str) -> None:
    repo: SQLiteRepository[ActionRecord] = SQLiteRepository(
        ActionRecord, database_url=tmp_db_url
    )
    await repo.save(ActionRecord(id="a1", match_id="m1", action_name="alert"))
    await repo.save(ActionRecord(id="a2", match_id="m1", action_name="draft_email"))
    await repo.save(
        ActionRecord(
            id="a3",
            match_id="m2",
            action_name="alert",
            status=ActionStatus.SUCCESS,
        )
    )

    successes = await repo.list(status=ActionStatus.SUCCESS)
    assert {a.id for a in successes} == {"a3"}

    alerts = await repo.list(action_name="alert")
    assert {a.id for a in alerts} == {"a1", "a3"}


@pytest.mark.asyncio
async def test_separate_entity_types_do_not_collide(tmp_db_url: str) -> None:
    matches: SQLiteRepository[Match] = SQLiteRepository(Match, database_url=tmp_db_url)
    actions: SQLiteRepository[ActionRecord] = SQLiteRepository(
        ActionRecord, database_url=tmp_db_url
    )

    await matches.save(
        Match(id="shared", job_id="j", confidence=0.5, risk=0.5, decision=Decision.ALERT)
    )
    await actions.save(ActionRecord(id="shared", match_id="m", action_name="alert"))

    assert (await matches.get("shared")) is not None
    assert (await actions.get("shared")) is not None


@pytest.mark.asyncio
async def test_delete_removes_only_targeted_row(tmp_db_url: str) -> None:
    repo: SQLiteRepository[Match] = SQLiteRepository(Match, database_url=tmp_db_url)
    await repo.save(
        Match(id="m1", job_id="j1", confidence=0.5, risk=0.5, decision=Decision.SKIP)
    )
    await repo.save(
        Match(id="m2", job_id="j2", confidence=0.5, risk=0.5, decision=Decision.SKIP)
    )

    await repo.delete("m1")
    assert (await repo.get("m1")) is None
    assert (await repo.get("m2")) is not None
