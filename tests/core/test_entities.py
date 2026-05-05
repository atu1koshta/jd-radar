"""Pure-domain entity sanity checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobhunter.core.entities import (
    ActionRecord,
    ActionStatus,
    Decision,
    EmailDraft,
    EmailDraftStatus,
    InterpretedResume,
    InterpretedSkill,
    Job,
    Match,
    Resume,
    canonical_body_hash,
)


def test_match_clips_confidence_and_risk_into_unit_interval() -> None:
    m = Match(
        id="m1",
        job_id="j1",
        confidence=1.7,
        risk=-0.2,
        decision=Decision.ALERT,
    )
    assert m.confidence == 1.0
    assert m.risk == 0.0


def test_match_decision_is_strenum() -> None:
    m = Match(id="m1", job_id="j1", confidence=0.5, risk=0.5, decision="DRAFT")
    assert m.decision is Decision.DRAFT
    assert m.decision == "DRAFT"


def test_job_requires_url_and_core_fields() -> None:
    with pytest.raises(ValidationError):
        Job(id="j1", portal="naukri", external_id="x", title="t", company="c")  # type: ignore[call-arg]


def test_action_record_defaults_to_pending() -> None:
    a = ActionRecord(id="a1", match_id="m1", action_name="alert")
    assert a.status is ActionStatus.PENDING


def test_email_draft_starts_pending_review() -> None:
    d = EmailDraft(id="d1", job_id="j1", to="x@y.z", subject="hi", body="hello")
    assert d.status is EmailDraftStatus.PENDING_REVIEW
    assert d.sent_at is None


# ---- Resume + InterpretedResume -----------------------------------------


def test_resume_default_has_empty_body_and_no_interpretation() -> None:
    r = Resume()
    assert r.body == {}
    assert r.interpreted is None
    assert r.body_hash == ""


def test_resume_preserves_arbitrary_top_level_yaml_keys_in_body() -> None:
    r = Resume.model_validate(
        {
            "body": {"some_custom_key": [1, 2], "nested": {"x": "y"}},
            "body_hash": "abc",
        }
    )
    assert r.body["some_custom_key"] == [1, 2]
    assert r.body["nested"] == {"x": "y"}


def test_canonical_body_hash_stable_under_key_reorder() -> None:
    a = {"name": "Atul", "skills": ["Python", "FastAPI"]}
    b = {"skills": ["Python", "FastAPI"], "name": "Atul"}
    assert canonical_body_hash(a) == canonical_body_hash(b)


def test_canonical_body_hash_changes_when_value_changes() -> None:
    a = {"name": "Atul"}
    b = {"name": "Atul Koshta"}
    assert canonical_body_hash(a) != canonical_body_hash(b)


def test_interpreted_resume_requires_summary_and_body_hash() -> None:
    with pytest.raises(ValidationError):
        InterpretedResume(body_hash="abc")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        InterpretedResume(summary="...")  # type: ignore[call-arg]


def test_interpreted_resume_minimal_construction() -> None:
    ir = InterpretedResume(
        summary="Software architect with 8 years building backend services.",
        body_hash="abc",
        total_experience_years=8.0,
        seniority_level="senior",
        skills=[InterpretedSkill(name="Python", category="language", years=8)],
    )
    assert ir.skills[0].category == "language"
    assert ir.seniority_level == "senior"
    assert ir.search_query_terms == []
