"""Decide which Actions run for a Match.

The bucket → action mapping is the single place the system encodes "what
do we do for this kind of match?" Adding a new action plugin (auto-apply,
calendar, ...) only requires:
1. Implementing the `Action` Protocol.
2. Registering it under `[project.entry-points."jobhunter.actions"]`.
3. Listing it in `ENABLED_ACTIONS`.

The decision logic stays here, deliberately small and grep-able.
"""

from __future__ import annotations

from collections.abc import Iterable

from jobhunter.core.entities import Decision, Match
from jobhunter.ports.action import Action


def decide_actions(
    *,
    match: Match,
    available_actions: Iterable[Action],
) -> list[Action]:
    """Return the ordered list of Actions to run for this Match.

    v1 mapping:
        SKIP  -> []                                (no side effects)
        ALERT -> [AlertAction]                     (Telegram only)
        DRAFT -> [AlertAction, DraftEmailAction]   (alert + email draft)

    Each Action also gets a final `is_applicable(ctx)` check at the
    execute-time call site, so individual actions can opt out of a match
    even when the decision bucket would otherwise include them.
    """
    if match.decision == Decision.SKIP:
        return []

    by_name = {a.name: a for a in available_actions}

    selected: list[str] = ["alert"] if match.decision in (Decision.ALERT, Decision.DRAFT) else []
    if match.decision == Decision.DRAFT:
        selected.append("draft_email")

    out: list[Action] = []
    for name in selected:
        action = by_name.get(name)
        if action is not None:
            out.append(action)
    return out
