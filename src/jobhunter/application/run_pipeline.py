"""End-to-end orchestrator (Phase 4 preview).

Stitches together every previously-built use case:

    load_resume → portal.search → portal.fetch_jd → score_match
    → decide_actions → execute_actions → persist

State carries between stages in-memory; SQLite holds the durable artifacts
(`Resume`, `Job`, `Match`, `EmailDraft`, `ActionRecord`). v1 is single-pass:
no queue, no retries, no scheduler. Phase 4 proper layers those on.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from jobhunter.application.decide_actions import decide_actions
from jobhunter.application.execute_actions import execute_actions
from jobhunter.application.load_resume import load_resume
from jobhunter.application.score_match import score_match
from jobhunter.bootstrap.container import Container
from jobhunter.core.entities import (
    Decision,
    Job,
    JobQuery,
    Match,
    Resume,
)
from jobhunter.core.errors import ConfigError, JobHunterError
from jobhunter.ports.action import Action, ActionContext


@dataclass
class PipelineRunReport:
    portal: str
    query: str
    jobs_seen: int = 0
    matches_drafted: int = 0
    matches_alerted: int = 0
    matches_skipped: int = 0
    failures: int = 0
    actions_succeeded: int = 0
    actions_failed: int = 0


async def run_pipeline(
    *,
    container: Container,
    portal_name: str,
    query: JobQuery,
    limit: int = 5,
    refresh_resume: bool = False,
) -> PipelineRunReport:
    """Run the full search → score → act loop against one portal.

    Returns a `PipelineRunReport` summarising counts; per-job artifacts
    are already persisted to SQLite by the time this returns.
    """
    if container.resume_loader is None or container.llm is None:
        raise ConfigError("container missing resume_loader / llm — call build_container() first")

    report = PipelineRunReport(portal=portal_name, query=query.keywords)

    resume: Resume = await load_resume(container.resume_loader, force_refresh=refresh_resume)
    if resume.interpreted is None:
        raise ConfigError(
            "resume.interpreted is None after load — run `jobhunter load-resume --refresh`"
        )
    logger.info(
        "pipeline: resume loaded ({} skills, seniority={})",
        len(resume.interpreted.skills),
        resume.interpreted.seniority_level,
    )

    available_actions = _instantiate_enabled_actions(container)
    logger.info(
        "pipeline: {} action(s) available: {}",
        len(available_actions),
        [a.name for a in available_actions],
    )

    portal = container.build_portal(portal_name)
    try:
        async for job in portal.search(query, limit=limit):
            report.jobs_seen += 1
            try:
                job = await portal.fetch_jd(job)
                match = await _score_and_persist(
                    container=container, resume=resume, job=job
                )
                _bump_decision_counter(report, match.decision)
                results = await _act_on_match(
                    container=container,
                    resume=resume,
                    job=job,
                    match=match,
                    actions=available_actions,
                )
                report.actions_succeeded += sum(1 for r in results if r.outcome == "success")
                report.actions_failed += sum(1 for r in results if r.outcome == "failed")
            except JobHunterError as e:
                report.failures += 1
                logger.warning("pipeline: job {} failed: {}", job.id, e)
            except Exception as e:  # noqa: BLE001
                report.failures += 1
                logger.warning(
                    "pipeline: job {} raised {}: {}", job.id, type(e).__name__, e
                )
    finally:
        await portal.close()

    return report


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _instantiate_enabled_actions(container: Container) -> list[Action]:
    """Build one instance of every action listed in `ENABLED_ACTIONS`.

    Unknown / unregistered action names are skipped with a warning so a
    typo in `.env` doesn't kill the whole run.
    """
    enabled = container.settings.enabled_actions
    out: list[Action] = []
    for name in enabled:
        try:
            out.append(container.build_action(name))
        except Exception as e:  # noqa: BLE001
            logger.warning("pipeline: action '{}' could not be built ({})", name, e)
    return out


def _bump_decision_counter(report: PipelineRunReport, decision: Decision) -> None:
    if decision == Decision.SKIP:
        report.matches_skipped += 1
    elif decision == Decision.ALERT:
        report.matches_alerted += 1
    elif decision == Decision.DRAFT:
        report.matches_drafted += 1


async def _score_and_persist(
    *, container: Container, resume: Resume, job: Job
) -> Match:
    match, _rubric = await score_match(
        resume=resume,
        jd_text=job.jd_raw or "",
        llm=container.llm,  # type: ignore[arg-type]
        risk_tolerance=container.settings.risk_tolerance,
        job_id=job.id,
    )
    if container.match_repo is not None:
        await container.match_repo.save(match)
    return match


async def _act_on_match(
    *,
    container: Container,
    resume: Resume,
    job: Job,
    match: Match,
    actions: list[Action],
) -> list:
    chosen = decide_actions(match=match, available_actions=actions)
    if not chosen:
        logger.debug("pipeline: no actions for job {} (decision={})", job.id, match.decision)
        return []

    ctx = ActionContext(
        job=job,
        match=match,
        ports={
            "llm": container.llm,
            "notifier": container.notifier,
            "email_sender": container.email_sender,
            "draft_repo": container.draft_repo,
            "match_repo": container.match_repo,
            "resume": resume,
        },
        settings={
            "dry_run": container.settings.dry_run,
            "risk_tolerance": container.settings.risk_tolerance,
        },
    )
    return await execute_actions(
        ctx=ctx, actions=chosen, record_repo=container.action_record_repo
    )
