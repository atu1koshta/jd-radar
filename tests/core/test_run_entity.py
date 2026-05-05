"""Run entity sanity."""

from __future__ import annotations

from jobhunter.core.entities import Run, RunCounters, RunStatus


def test_run_default_status_is_running_with_zero_counters() -> None:
    r = Run(id="run:1", portal="naukri", query="x")
    assert r.status is RunStatus.RUNNING
    assert r.counters.jobs_seen == 0
    assert r.counters.actions_succeeded == 0
    assert r.ended_at is None


def test_run_counters_increment_independently() -> None:
    c = RunCounters()
    c.jobs_seen += 1
    c.matches_drafted += 2
    c.actions_failed += 3
    assert c.jobs_seen == 1
    assert c.matches_drafted == 2
    assert c.actions_failed == 3
