"""AlertAction — push a Telegram message summarising a Match.

Pulls only the `notifier` port from the action context. No LLM call: the
alert text is templated from already-computed `Match` + `Job` fields so
the user gets the alert in milliseconds, not seconds.
"""

from __future__ import annotations

from loguru import logger

from jobhunter.core.entities import Match
from jobhunter.ports.action import ActionContext, ActionOutcome, ActionResult
from jobhunter.ports.notifier import Notification, NotificationChannel


class AlertAction:
    name = "alert"

    async def is_applicable(self, ctx: ActionContext) -> bool:
        # Both ALERT and DRAFT decisions warrant a Telegram ping.
        return str(ctx.match.decision) in {"ALERT", "DRAFT"}

    async def execute(self, ctx: ActionContext) -> ActionResult:
        notifier = ctx.ports.get("notifier")
        if not isinstance(notifier, NotificationChannel):
            return ActionResult(
                name=self.name,
                outcome=ActionOutcome.FAILED,
                message="`notifier` port missing in ActionContext",
            )
        msg = self._render(ctx)
        result = await notifier.send(msg)

        if not result.delivered:
            logger.warning("alert action failed: {}", result.error)
            return ActionResult(
                name=self.name,
                outcome=ActionOutcome.FAILED,
                message=result.error,
                payload={"channel": result.channel},
            )
        return ActionResult(
            name=self.name,
            outcome=ActionOutcome.SUCCESS,
            payload={
                "channel": result.channel,
                "message_id": result.message_id or "",
            },
        )

    @staticmethod
    def _render(ctx: ActionContext) -> Notification:
        m: Match = ctx.match
        j = ctx.job
        title = f"[{m.decision}] {j.title}"
        body_lines = [
            f"Company: {j.company}",
            f"Confidence: {m.confidence:.2f}   Risk: {m.risk:.2f}",
            f"Portal: {j.portal}",
            f"URL: {j.url}",
        ]
        reasoning = m.breakdown.get("reasoning") if isinstance(m.breakdown, dict) else None
        if reasoning:
            body_lines.append("")
            body_lines.append(str(reasoning))
        return Notification(
            kind="match_alert",
            title=title,
            body="\n".join(body_lines),
            metadata={
                "job_id": j.id,
                "match_id": m.id,
                "decision": str(m.decision),
            },
        )
