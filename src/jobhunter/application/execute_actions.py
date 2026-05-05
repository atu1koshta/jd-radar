"""Iterate the chosen Actions, persist `ActionRecord` per attempt.

No retries / backoff in v1 — actions are inherently network-dependent and
half-success states (alert posted but draft persisted twice) cost more
than the occasional missed alert. Phase 5 layers retry decorators around
the call site, behind the `Action` port, when we actually need them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from loguru import logger

from jobhunter.core.entities import ActionRecord, ActionStatus
from jobhunter.ports.action import Action, ActionContext, ActionOutcome, ActionResult
from jobhunter.ports.repository import Repository


_OUTCOME_TO_STATUS = {
    ActionOutcome.SUCCESS: ActionStatus.SUCCESS,
    ActionOutcome.SKIPPED: ActionStatus.SKIPPED,
    ActionOutcome.FAILED: ActionStatus.FAILED,
}


async def execute_actions(
    *,
    ctx: ActionContext,
    actions: Sequence[Action],
    record_repo: Repository[ActionRecord] | None = None,
) -> list[ActionResult]:
    """Run each action in order. Returns every produced `ActionResult`.

    Action exceptions are caught and converted into `FAILED` records so a
    single bad adapter doesn't poison the rest of the action queue. The
    exception is re-logged at WARNING level for the operator.
    """
    results: list[ActionResult] = []
    for action in actions:
        try:
            applicable = await action.is_applicable(ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning("action {} is_applicable raised {}: {}", action.name, type(e).__name__, e)
            applicable = False

        if not applicable:
            results.append(
                ActionResult(
                    name=action.name,
                    outcome=ActionOutcome.SKIPPED,
                    message="is_applicable returned False",
                )
            )
            await _persist(record_repo, ctx, results[-1])
            continue

        try:
            result = await action.execute(ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "action {} execute raised {}: {}", action.name, type(e).__name__, e
            )
            result = ActionResult(
                name=action.name,
                outcome=ActionOutcome.FAILED,
                message=f"{type(e).__name__}: {e}",
            )

        results.append(result)
        await _persist(record_repo, ctx, result)
    return results


async def _persist(
    repo: Repository[ActionRecord] | None,
    ctx: ActionContext,
    result: ActionResult,
) -> None:
    if repo is None:
        return
    record = ActionRecord(
        id=f"actrec:{uuid.uuid4().hex[:12]}",
        match_id=ctx.match.id,
        action_name=result.name,
        status=_OUTCOME_TO_STATUS[result.outcome],
        payload=result.payload,
        result={"message": result.message or ""},
        error=result.message if result.outcome == ActionOutcome.FAILED else None,
    )
    try:
        await repo.save(record)
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to persist ActionRecord for {}: {}", result.name, e)
