"""decide_actions: maps a Match's Decision bucket to an ordered Action list."""

from __future__ import annotations

import pytest

from jobhunter.application.decide_actions import decide_actions
from jobhunter.core.entities import Decision, Match


class _NamedAction:
    def __init__(self, name: str) -> None:
        self.name = name

    async def is_applicable(self, ctx) -> bool:  # pragma: no cover
        return True

    async def execute(self, ctx):  # pragma: no cover
        raise AssertionError("not used in decide_actions tests")


@pytest.fixture
def actions() -> list[_NamedAction]:
    return [_NamedAction("alert")]


def _match(decision: Decision) -> Match:
    return Match(id="m", job_id="j", confidence=0.5, risk=0.5, decision=decision)


def test_skip_returns_no_actions(actions: list[_NamedAction]) -> None:
    out = decide_actions(match=_match(Decision.SKIP), available_actions=actions)
    assert out == []


def test_alert_returns_only_alert_action(actions: list[_NamedAction]) -> None:
    out = decide_actions(match=_match(Decision.ALERT), available_actions=actions)
    assert [a.name for a in out] == ["alert"]


def test_missing_alert_action_in_registry_is_silently_skipped() -> None:
    out = decide_actions(match=_match(Decision.ALERT), available_actions=[])
    assert out == []
