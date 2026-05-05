"""Risk + decision math. Pure functions only.

`decide()` translates (confidence, risk, risk_tolerance) into one of the
two v1 decisions used by `application/decide_actions.py`. The Action
subsystem then maps those into concrete actions (alert, ...).
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


def decide(*, confidence: float, risk_tolerance: float = 0.5) -> Decision:
    """Bucket a Match into SKIP / ALERT.

    - ALERT requires confidence >= risk_tolerance.
    - Otherwise SKIP.
    """
    if _clip01(confidence) >= _clip01(risk_tolerance):
        return Decision.ALERT
    return Decision.SKIP
