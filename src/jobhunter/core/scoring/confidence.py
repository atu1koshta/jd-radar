"""Pure confidence math.

LLM-derived component scores feed in; this module never calls any IO. That
keeps the scoring formula testable in isolation and identical regardless of
which backend produced the components.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobhunter.core.errors import ScoringError

# Weights chosen to match the design plan; sum must equal 1.0.
W_SKILL = 0.5
W_TITLE = 0.3
W_EXPERIENCE = 0.2

assert abs(W_SKILL + W_TITLE + W_EXPERIENCE - 1.0) < 1e-9  # weights must sum to 1


@dataclass(frozen=True)
class ConfidenceComponents:
    """Three sub-scores in [0, 1] that combine into final confidence."""

    skill_overlap: float
    title_match: float
    experience_fit: float


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def compute_confidence(c: ConfidenceComponents) -> tuple[float, dict[str, float]]:
    """Return (confidence, breakdown). Breakdown is suitable for `Match.breakdown`.

    Out-of-range component values are clipped silently so a noisy LLM rubric
    can't crash the pipeline.
    """
    skill = _clip01(c.skill_overlap)
    title = _clip01(c.title_match)
    exp = _clip01(c.experience_fit)

    confidence = _clip01(W_SKILL * skill + W_TITLE * title + W_EXPERIENCE * exp)

    breakdown = {
        "skill_overlap": skill,
        "title_match": title,
        "experience_fit": exp,
        "weight_skill": W_SKILL,
        "weight_title": W_TITLE,
        "weight_experience": W_EXPERIENCE,
        "confidence": confidence,
    }
    return confidence, breakdown


def experience_fit_from_years(
    *, years_held: float | None, years_required: float | None
) -> float:
    """Map (years_held, years_required) to a [0, 1] fit score.

    Rules:
    - Either side missing → neutral 0.5 (we don't know enough to penalise).
    - Held >= required → 1.0
    - Held < required → ratio (held / required), but never below 0.

    Pure function. No IO. No exceptions raised on unknown input — caller
    decides how to surface that.
    """
    if years_required is None or years_held is None:
        return 0.5
    if years_required <= 0:
        return 1.0
    if years_held < 0:
        raise ScoringError(f"years_held cannot be negative: {years_held}")
    return _clip01(years_held / years_required)
