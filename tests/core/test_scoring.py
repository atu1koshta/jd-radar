"""Pure-math scoring tests: confidence, risk, decision."""

from __future__ import annotations

import pytest

from jobhunter.core.entities import Decision
from jobhunter.core.errors import ScoringError
from jobhunter.core.scoring.confidence import (
    W_EXPERIENCE,
    W_SKILL,
    W_TITLE,
    ConfidenceComponents,
    compute_confidence,
    experience_fit_from_years,
)
from jobhunter.core.scoring.risk_gate import compute_risk, decide


# ---- compute_confidence ---------------------------------------------------


def test_weights_sum_to_one() -> None:
    assert pytest.approx(W_SKILL + W_TITLE + W_EXPERIENCE, abs=1e-9) == 1.0


def test_perfect_components_yield_full_confidence() -> None:
    conf, _ = compute_confidence(
        ConfidenceComponents(skill_overlap=1.0, title_match=1.0, experience_fit=1.0)
    )
    assert conf == 1.0


def test_zero_components_yield_zero_confidence() -> None:
    conf, _ = compute_confidence(
        ConfidenceComponents(skill_overlap=0.0, title_match=0.0, experience_fit=0.0)
    )
    assert conf == 0.0


def test_confidence_uses_documented_weights() -> None:
    conf, breakdown = compute_confidence(
        ConfidenceComponents(skill_overlap=0.8, title_match=0.5, experience_fit=0.4)
    )
    expected = 0.5 * 0.8 + 0.3 * 0.5 + 0.2 * 0.4
    assert pytest.approx(conf, abs=1e-9) == expected
    assert breakdown["weight_skill"] == 0.5
    assert breakdown["weight_title"] == 0.3
    assert breakdown["weight_experience"] == 0.2


def test_components_out_of_range_are_silently_clipped() -> None:
    conf, breakdown = compute_confidence(
        ConfidenceComponents(skill_overlap=1.7, title_match=-0.4, experience_fit=0.5)
    )
    # 0.5 * 1.0 + 0.3 * 0.0 + 0.2 * 0.5 = 0.6
    assert pytest.approx(conf, abs=1e-9) == 0.6
    assert breakdown["skill_overlap"] == 1.0
    assert breakdown["title_match"] == 0.0


# ---- experience_fit_from_years -------------------------------------------


def test_experience_fit_neutral_when_either_side_missing() -> None:
    assert experience_fit_from_years(years_held=None, years_required=5) == 0.5
    assert experience_fit_from_years(years_held=5, years_required=None) == 0.5
    assert experience_fit_from_years(years_held=None, years_required=None) == 0.5


def test_experience_fit_full_when_held_meets_or_exceeds() -> None:
    assert experience_fit_from_years(years_held=8, years_required=5) == 1.0
    assert experience_fit_from_years(years_held=5, years_required=5) == 1.0


def test_experience_fit_zero_required_treated_as_full() -> None:
    assert experience_fit_from_years(years_held=2, years_required=0) == 1.0


def test_experience_fit_partial_when_underqualified() -> None:
    assert experience_fit_from_years(years_held=2, years_required=4) == 0.5


def test_experience_fit_rejects_negative_years_held() -> None:
    with pytest.raises(ScoringError):
        experience_fit_from_years(years_held=-1, years_required=5)


# ---- compute_risk ---------------------------------------------------------


def test_risk_is_inverse_of_confidence_when_no_anti_bot_signal() -> None:
    assert compute_risk(confidence=1.0) == 0.0
    assert compute_risk(confidence=0.7) == pytest.approx(0.3, abs=1e-9)
    assert compute_risk(confidence=0.0) == 1.0


def test_anti_bot_signal_pushes_risk_up_and_clips_at_one() -> None:
    assert compute_risk(confidence=0.5, portal_anti_bot_score=0.2) == pytest.approx(0.7, abs=1e-9)
    assert compute_risk(confidence=0.0, portal_anti_bot_score=0.5) == 1.0


# ---- decide ---------------------------------------------------------------


def test_decide_skip_below_alert_threshold() -> None:
    assert decide(confidence=0.4, risk=0.6, risk_tolerance=0.3) is Decision.SKIP


def test_decide_alert_at_medium_confidence() -> None:
    assert decide(confidence=0.6, risk=0.4, risk_tolerance=0.3) is Decision.ALERT


def test_decide_draft_requires_high_confidence_and_low_risk() -> None:
    assert decide(confidence=0.85, risk=0.15, risk_tolerance=0.3) is Decision.DRAFT


def test_decide_falls_back_to_alert_when_risk_exceeds_tolerance() -> None:
    # confidence is high enough on its own, but risk blows past the tolerance.
    assert decide(confidence=0.9, risk=0.5, risk_tolerance=0.3) is Decision.ALERT


def test_decide_higher_risk_tolerance_lowers_draft_threshold() -> None:
    # confidence 0.6 is below the default DRAFT bar (0.7 with tol=0.3) ...
    assert decide(confidence=0.6, risk=0.4, risk_tolerance=0.3) is Decision.ALERT
    # ... but with tolerance bumped to 0.5 the same match becomes DRAFT.
    assert decide(confidence=0.6, risk=0.4, risk_tolerance=0.5) is Decision.DRAFT
