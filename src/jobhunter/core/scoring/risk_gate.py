"""Risk + decision math. Pure functions only.

`decide()` translates (confidence, risk, risk_tolerance) into one of the
three v1 decisions used by `application/decide_actions.py`. The Action
subsystem then maps those into concrete actions (alert, draft email, ...).
"""

from __future__ import annotations

from jobhunter.core.entities import Decision


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def compute_risk(*, confidence: float, portal_anti_bot_score: float = 0.0) -> float:
    """Higher is riskier. Anti-bot score nudges risk up regardless of fit.

    `portal_anti_bot_score` reserved for Phase 2 once we measure portal
    flakiness; defaults to 0 today so risk == 1 - confidence.
    """
    return _clip01((1.0 - _clip01(confidence)) + _clip01(portal_anti_bot_score))


def decide(
    *,
    confidence: float,
    risk: float,
    risk_tolerance: float,
) -> Decision:
    """Bucket a Match into SKIP / ALERT / DRAFT.

    - DRAFT requires confidence >= 1 - risk_tolerance AND risk <= risk_tolerance.
      A DRAFT match becomes [AlertAction, DraftEmailAction].
    - ALERT requires confidence >= 0.5.
    - Otherwise SKIP.
    """
    c = _clip01(confidence)
    r = _clip01(risk)
    t = _clip01(risk_tolerance)

    if c >= (1.0 - t) and r <= t:
        return Decision.DRAFT
    if c >= 0.5:
        return Decision.ALERT
    return Decision.SKIP
