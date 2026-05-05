"""DraftEmailAction — generate + persist a personalised cold-outreach draft.

Pulls `llm` (for body generation), `draft_repo` (Repository[EmailDraft]),
and `match_repo` (Repository[Match]) from the action context. Drafts land
in the DB with `status=pending_review`. The user reviews them via
`jobhunter review` and dispatches with `jobhunter send-draft <id>`.

The recipient (`to`) is best-effort: portals rarely surface the hiring
manager email directly, so v1 leaves it blank for the user to fill before
sending. The body still goes out fully written, so the human edit is
trivial.
"""

from __future__ import annotations

import textwrap
import uuid

from pydantic import BaseModel, Field

from jobhunter.core.entities import EmailDraft, Resume
from jobhunter.ports.action import ActionContext, ActionOutcome, ActionResult
from jobhunter.ports.llm import LLMProvider, Prompt
from jobhunter.ports.repository import Repository


class _DraftPayload(BaseModel):
    """Strict Pydantic schema the LLM must fill."""

    subject: str = Field(description="Concise subject line, < 80 chars")
    body: str = Field(description="Plain-text email body, third-person courteous")
    suggested_to: str | None = Field(
        default=None,
        description="If the JD mentions a recruiter / hiring manager email, surface it here.",
    )


class DraftEmailAction:
    name = "draft_email"

    async def is_applicable(self, ctx: ActionContext) -> bool:
        # Only draft for high-confidence matches.
        return str(ctx.match.decision) == "DRAFT"

    async def execute(self, ctx: ActionContext) -> ActionResult:
        llm = ctx.ports.get("llm")
        draft_repo = ctx.ports.get("draft_repo")
        resume = ctx.ports.get("resume")

        if not isinstance(llm, LLMProvider):
            return ActionResult(
                name=self.name,
                outcome=ActionOutcome.FAILED,
                message="`llm` port missing in ActionContext",
            )
        if draft_repo is None:
            return ActionResult(
                name=self.name,
                outcome=ActionOutcome.FAILED,
                message="`draft_repo` missing in ActionContext",
            )

        prompt = self._build_prompt(ctx, resume if isinstance(resume, Resume) else None)
        try:
            payload = await llm.structured(prompt, _DraftPayload)
        except Exception as e:  # noqa: BLE001
            return ActionResult(
                name=self.name,
                outcome=ActionOutcome.FAILED,
                message=f"LLM draft generation failed: {type(e).__name__}: {e}",
            )

        draft = EmailDraft(
            id=f"draft:{uuid.uuid4().hex[:12]}",
            job_id=ctx.job.id,
            to=(payload.suggested_to or "").strip(),
            subject=payload.subject.strip(),
            body=payload.body.strip(),
        )
        repo: Repository[EmailDraft] = draft_repo  # type: ignore[assignment]
        await repo.save(draft)

        return ActionResult(
            name=self.name,
            outcome=ActionOutcome.SUCCESS,
            payload={
                "draft_id": draft.id,
                "to": draft.to,
                "subject": draft.subject,
            },
        )

    @staticmethod
    def _build_prompt(ctx: ActionContext, resume: Resume | None) -> Prompt:
        m = ctx.match
        j = ctx.job
        resume_summary = (
            resume.interpreted.summary
            if resume and resume.interpreted
            else "(resume summary unavailable)"
        )
        candidate_skills = (
            ", ".join(s.name for s in resume.interpreted.skills[:10])
            if resume and resume.interpreted
            else "(skills unavailable)"
        )
        system = textwrap.dedent(
            """\
            You write short, sharp cold-outreach emails for a software engineer
            applying for jobs. The candidate is the SENDER; the company is the
            RECIPIENT. Tone: confident, courteous, specific to the role.

            Strict rules:
            - 4 short paragraphs maximum.
            - Reference at least one concrete fact from the JD (e.g. their stack
              or product) so the recipient knows the email isn't a blast.
            - Reference 2-3 of the candidate's relevant skills.
            - End with a clear, light ask (a 15-min intro chat, not "let me know").
            - No bullet points. No emojis. No exaggerated claims.
            - Subject line < 80 chars, no clickbait.

            Return ONLY the structured JSON. Do not narrate.
            """
        ).strip()
        user = textwrap.dedent(
            f"""\
            === JOB ===
            Title: {j.title}
            Company: {j.company}
            URL: {j.url}

            === JD (excerpt) ===
            {(j.jd_raw or '(no JD body)').strip()[:3000]}

            === CANDIDATE ===
            Summary: {resume_summary}
            Top skills: {candidate_skills}

            === MATCH ===
            Confidence: {m.confidence:.2f}   Risk: {m.risk:.2f}
            Decision: {m.decision}
            """
        )
        return Prompt(system=system, user=user, temperature=0.4)
