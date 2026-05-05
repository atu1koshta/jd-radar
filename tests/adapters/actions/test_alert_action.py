"""AlertAction: posts a Notification through the injected NotificationChannel."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import HttpUrl

from jobhunter.adapters.actions.alert import AlertAction
from jobhunter.core.entities import Decision, Job, Match
from jobhunter.ports.action import ActionContext, ActionOutcome
from jobhunter.ports.notifier import Notification, NotificationResult


class _RecordingNotifier:
    name = "recording"

    def __init__(self, *, deliver: bool = True, error: str | None = None) -> None:
        self.sent: list[Notification] = []
        self._deliver = deliver
        self._error = error

    async def send(self, msg: Notification) -> NotificationResult:
        self.sent.append(msg)
        return NotificationResult(
            delivered=self._deliver,
            channel=self.name,
            message_id="m1" if self._deliver else None,
            error=self._error,
        )

    async def health_check(self) -> bool:
        return True


def _ctx(decision: Decision, *, ports: dict[str, Any]) -> ActionContext:
    job = Job(
        id="naukri:1",
        portal="naukri",
        external_id="1",
        url=HttpUrl("https://www.naukri.com/x"),
        title="Senior Backend Engineer",
        company="Acme",
    )
    match = Match(
        id="m1",
        job_id=job.id,
        confidence=0.85,
        risk=0.15,
        decision=decision,
        breakdown={"reasoning": "strong python+aws fit"},
    )
    return ActionContext(job=job, match=match, ports=ports)


@pytest.mark.asyncio
async def test_is_applicable_only_for_alert() -> None:
    a = AlertAction()
    assert await a.is_applicable(_ctx(Decision.ALERT, ports={})) is True
    assert await a.is_applicable(_ctx(Decision.SKIP, ports={})) is False


@pytest.mark.asyncio
async def test_execute_pushes_a_notification_with_match_metadata() -> None:
    notifier = _RecordingNotifier()
    result = await AlertAction().execute(
        _ctx(Decision.ALERT, ports={"notifier": notifier})
    )
    assert result.outcome is ActionOutcome.SUCCESS
    assert len(notifier.sent) == 1
    n = notifier.sent[0]
    assert n.kind == "match_alert"
    assert "Acme" in n.body
    assert n.metadata["job_id"] == "naukri:1"
    assert n.metadata["decision"] == "ALERT"


@pytest.mark.asyncio
async def test_execute_reports_failure_when_notifier_fails() -> None:
    notifier = _RecordingNotifier(deliver=False, error="HTTP 401")
    result = await AlertAction().execute(
        _ctx(Decision.ALERT, ports={"notifier": notifier})
    )
    assert result.outcome is ActionOutcome.FAILED
    assert "401" in (result.message or "")


@pytest.mark.asyncio
async def test_missing_notifier_port_yields_failed_result() -> None:
    result = await AlertAction().execute(_ctx(Decision.ALERT, ports={}))
    assert result.outcome is ActionOutcome.FAILED
    assert "notifier" in (result.message or "")
