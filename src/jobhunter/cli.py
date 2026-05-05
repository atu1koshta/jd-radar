"""Typer-based CLI entry point."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime

import typer
from loguru import logger
from pydantic import BaseModel, Field

from pathlib import Path

from jobhunter.application.load_resume import load_resume as load_resume_uc
from jobhunter.application.score_match import score_match as score_match_uc
from jobhunter.bootstrap.container import build_container
from jobhunter.core.errors import JobHunterError
from jobhunter.ports.llm import Prompt

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _configure_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), enqueue=False)


# -----------------------------------------------------------------------
# test-llm — Phase 0 exit gate
# -----------------------------------------------------------------------

class JobMatchSmoke(BaseModel):
    """Tiny schema used purely to verify structured output round-trip."""

    title: str = Field(description="Suggested job title")
    confidence: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    reasoning: str


@app.command("test-llm")
def test_llm() -> None:
    """Round-trip a structured-output call against the configured LLM backend."""
    container = build_container()
    _configure_logging(container.settings.log_level)

    logger.info(
        "LLM backend={} model={} ctx={}",
        container.settings.llm_backend,
        container.settings.llm_model,
        container.settings.llm_num_ctx,
    )

    prompt = Prompt(
        system=(
            "You are a strict skill-matching assistant. "
            "Score how well a Python+FastAPI+Postgres backend resume fits a "
            "'Senior Backend Engineer (Python, Django, MySQL, AWS)' role. "
            "Return only the structured JSON."
        ),
        user="Score the match. Use confidence and risk in [0,1].",
        temperature=0.1,
    )

    async def _run() -> JobMatchSmoke:
        return await container.llm.structured(prompt, JobMatchSmoke)

    try:
        result = asyncio.run(_run())
    except JobHunterError as e:
        logger.error("LLM call failed: {}", e)
        raise typer.Exit(code=2) from e

    typer.echo(json.dumps(result.model_dump(), indent=2))


# -----------------------------------------------------------------------
# load-resume — Phase 1: TTL-driven refresh from GitHub
# -----------------------------------------------------------------------


@app.command("load-resume")
def load_resume_cmd(
    refresh: bool = typer.Option(
        False,
        "--refresh",
        "-r",
        help="Force a fetch even if the cached copy is younger than the TTL.",
    ),
    show_raw: bool = typer.Option(
        False,
        "--raw",
        help="Print the entire raw YAML body instead of the parsed summary.",
    ),
) -> None:
    """Fetch the resume from GitHub if the cache is older than `RESUME_REFRESH_TTL_MIN`,
    persist it to the DB, and print a short summary."""
    container = build_container()
    _configure_logging(container.settings.log_level)

    logger.info(
        "resume source={} ttl_min={} cache={}",
        container.settings.resume_url,
        container.settings.resume_refresh_ttl_min,
        container.settings.resume_cache_path,
    )

    async def _run() -> None:
        resume = await load_resume_uc(container.resume_loader, force_refresh=refresh)
        age_s = (datetime.utcnow() - resume.last_updated_at).total_seconds()
        interpreted = resume.interpreted
        summary = {
            "id": resume.id,
            "body_top_level_keys": list(resume.body.keys()),
            "body_hash": resume.body_hash[:12],
            "last_updated_at": resume.last_updated_at.isoformat() + "Z",
            "age_seconds": int(age_s),
            "etag": resume.etag,
            "interpreted": (
                {
                    "name": interpreted.canonical_name,
                    "headline": interpreted.headline,
                    "seniority_level": interpreted.seniority_level,
                    "total_experience_years": interpreted.total_experience_years,
                    "skills_count": len(interpreted.skills),
                    "experiences_count": len(interpreted.experiences),
                    "domains": interpreted.domains,
                    "role_categories": interpreted.role_categories,
                    "search_query_terms": interpreted.search_query_terms,
                    "summary": interpreted.summary,
                    "model_used": interpreted.model_used,
                    "interpreted_at": interpreted.interpreted_at.isoformat() + "Z",
                }
                if interpreted
                else None
            ),
        }
        typer.echo(json.dumps(summary, indent=2))
        if show_raw:
            typer.echo("\n--- raw YAML body ---")
            typer.echo(json.dumps(resume.body, indent=2, default=str))

    try:
        asyncio.run(_run())
    except JobHunterError as e:
        logger.error("resume load failed: {}", e)
        raise typer.Exit(code=2) from e


# -----------------------------------------------------------------------
# score — Phase 1: score a JD file against the active resume
# -----------------------------------------------------------------------


@app.command("score")
def score_cmd(
    jd: Path = typer.Option(
        ...,
        "--jd",
        exists=True,
        readable=True,
        help="Path to a plain-text job description file.",
    ),
    job_id: str = typer.Option(
        "job:cli-adhoc",
        "--job-id",
        help="Synthetic job id used in the resulting Match record.",
    ),
    refresh_resume: bool = typer.Option(
        False,
        "--refresh-resume",
        help="Force refetch the resume before scoring (bypasses TTL).",
    ),
    risk_tolerance_override: float | None = typer.Option(
        None,
        "--risk-tolerance",
        min=0.0,
        max=1.0,
        help="Override RISK_TOLERANCE for this run (0..1).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Also print the resume summary actually sent to the LLM.",
    ),
) -> None:
    """Score a job description against the active resume and print the Match."""
    from jobhunter.application.score_match import _resume_summary_for_prompt

    container = build_container()
    _configure_logging(container.settings.log_level)

    jd_text = jd.read_text(encoding="utf-8")
    risk_tolerance = (
        risk_tolerance_override
        if risk_tolerance_override is not None
        else container.settings.risk_tolerance
    )

    async def _run() -> None:
        resume = await load_resume_uc(
            container.resume_loader, force_refresh=refresh_resume
        )
        if debug:
            typer.echo("=== RESUME SENT TO LLM ===", err=True)
            typer.echo(_resume_summary_for_prompt(resume), err=True)
            typer.echo(f"\n=== JD ({jd}) ===", err=True)
            typer.echo(jd_text.strip(), err=True)
            typer.echo("\n=== SCORING ===", err=True)

        match, rubric = await score_match_uc(
            resume=resume,
            jd_text=jd_text,
            llm=container.llm,
            risk_tolerance=risk_tolerance,
            job_id=job_id,
        )
        out = {
            "match": match.model_dump(mode="json"),
            "rubric": rubric.model_dump(),
        }
        typer.echo(json.dumps(out, indent=2, default=str))

    try:
        asyncio.run(_run())
    except JobHunterError as e:
        logger.error("scoring failed: {}", e)
        raise typer.Exit(code=2) from e


# -----------------------------------------------------------------------
# portal-test — Phase 2: smoke a portal end-to-end (login + search + JD)
# -----------------------------------------------------------------------


@app.command("portal-test")
def portal_test_cmd(
    portal: str = typer.Argument(..., help="Registered portal name, e.g. 'naukri'"),
    query: str = typer.Option(
        "software engineer",
        "--query",
        "-q",
        help="Search keywords. Default mirrors the user's canonical query.",
    ),
    location: str | None = typer.Option(
        None, "--location", help="Optional location filter (portal-specific format)."
    ),
    limit: int = typer.Option(5, "--limit", min=1, max=50, help="Max jobs to fetch."),
    fetch_jds: bool = typer.Option(
        True,
        "--fetch-jds/--no-fetch-jds",
        help="Also navigate to each result's URL and extract the JD body.",
    ),
    headless: bool | None = typer.Option(
        None,
        "--headless/--headed",
        help="Override BROWSER_HEADLESS for this run. --headed is useful while validating selectors.",
    ),
) -> None:
    """End-to-end portal smoke: log in, search, optionally fetch each JD."""
    from jobhunter.core.entities import JobQuery

    container = build_container()
    _configure_logging(container.settings.log_level)

    if headless is not None:
        container.settings.browser_headless = headless  # one-shot override

    logger.info(
        "portal-test {} query={!r} location={!r} limit={} headless={}",
        portal,
        query,
        location,
        limit,
        container.settings.browser_headless,
    )

    async def _run() -> None:
        adapter = container.build_portal(portal)
        try:
            jq = JobQuery(keywords=query, location=location)
            jobs: list = []
            async for job in adapter.search(jq, limit=limit):
                if fetch_jds:
                    job = await adapter.fetch_jd(job)
                jobs.append(job)

            payload = [
                {
                    "id": j.id,
                    "title": j.title,
                    "company": j.company,
                    "url": str(j.url),
                    "jd_chars": len(j.jd_raw or ""),
                    "jd_content_hash": j.jd_content_hash[:12] if j.jd_content_hash else "",
                }
                for j in jobs
            ]
            typer.echo(json.dumps({"portal": portal, "count": len(jobs), "jobs": payload}, indent=2))
        finally:
            await adapter.close()

    try:
        asyncio.run(_run())
    except JobHunterError as e:
        logger.error("portal-test failed: {}", e)
        raise typer.Exit(code=2) from e


# -----------------------------------------------------------------------
# alert-test — Phase 3 smoke for the Telegram wiring
# -----------------------------------------------------------------------


@app.command("alert-test")
def alert_test_cmd(
    text: str = typer.Option(
        "jobhunter alert-test: pipeline wiring OK",
        "--text",
        help="Body of the test message.",
    ),
) -> None:
    """Push a single hello message through the configured NotificationChannel."""
    from jobhunter.ports.notifier import Notification

    container = build_container()
    _configure_logging(container.settings.log_level)

    if container.notifier is None:
        logger.error(
            "no notifier configured — set TELEGRAM_TOKEN + TELEGRAM_CHAT_ID in .env"
        )
        raise typer.Exit(code=2)

    async def _run() -> None:
        ok = await container.notifier.health_check()
        logger.info("notifier health_check: {}", ok)
        result = await container.notifier.send(
            Notification(kind="alert_test", title="alert-test", body=text)
        )
        typer.echo(json.dumps(result.model_dump(), indent=2))

    try:
        asyncio.run(_run())
    except JobHunterError as e:
        logger.error("alert-test failed: {}", e)
        raise typer.Exit(code=2) from e


# -----------------------------------------------------------------------
# review — list pending email drafts
# -----------------------------------------------------------------------


@app.command("review")
def review_cmd(
    show_body: bool = typer.Option(
        False, "--show-body", help="Print the full email body for each draft."
    ),
) -> None:
    """List EmailDrafts that are waiting for human approval."""
    from jobhunter.core.entities import EmailDraftStatus

    container = build_container()
    _configure_logging(container.settings.log_level)

    if container.draft_repo is None:
        raise typer.Exit(code=2)

    async def _run() -> None:
        drafts = await container.draft_repo.list(status=EmailDraftStatus.PENDING_REVIEW)
        if not drafts:
            typer.echo("(no pending drafts)")
            return
        for d in drafts:
            typer.echo(f"\n--- {d.id} -----------------------------------------")
            typer.echo(f"Job:     {d.job_id}")
            typer.echo(f"To:      {d.to or '(unset — fill with `send-draft --to`)'}")
            typer.echo(f"Subject: {d.subject}")
            typer.echo(f"Created: {d.created_at.isoformat()}")
            if show_body:
                typer.echo("")
                typer.echo(d.body)

    try:
        asyncio.run(_run())
    except JobHunterError as e:
        logger.error("review failed: {}", e)
        raise typer.Exit(code=2) from e


# -----------------------------------------------------------------------
# send-draft — manually approve + dispatch one email draft
# -----------------------------------------------------------------------


@app.command("send-draft")
def send_draft_cmd(
    draft_id: str = typer.Argument(..., help="EmailDraft id, e.g. draft:abc123def456"),
    to_override: str | None = typer.Option(
        None, "--to", help="Override the draft's recipient address."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Send (or, with LogOnlyEmailSender, log) one EmailDraft and mark it sent."""
    from jobhunter.core.entities import EmailDraftStatus
    from jobhunter.ports.email_sender import Email

    container = build_container()
    _configure_logging(container.settings.log_level)

    if container.draft_repo is None or container.email_sender is None:
        raise typer.Exit(code=2)

    async def _run() -> None:
        draft = await container.draft_repo.get(draft_id)
        if draft is None:
            logger.error("draft '{}' not found", draft_id)
            raise typer.Exit(code=2)
        if draft.status != EmailDraftStatus.PENDING_REVIEW:
            logger.error(
                "draft '{}' is in status '{}'; only pending_review drafts can be sent",
                draft.id,
                draft.status,
            )
            raise typer.Exit(code=2)

        recipient = (to_override or draft.to or "").strip()
        if not recipient:
            logger.error(
                "draft '{}' has no recipient. Re-run with --to <address>", draft.id
            )
            raise typer.Exit(code=2)

        if not yes:
            typer.echo(f"To:      {recipient}")
            typer.echo(f"Subject: {draft.subject}")
            typer.echo("--- body -----------------------------------------")
            typer.echo(draft.body)
            typer.echo("--------------------------------------------------")
            if not typer.confirm("Send this email?", default=False):
                typer.echo("aborted")
                raise typer.Exit(code=1)

        result = await container.email_sender.send(
            Email(to=recipient, subject=draft.subject, body=draft.body)
        )
        if not result.sent:
            logger.error("email_sender refused: {}", result.error)
            raise typer.Exit(code=2)

        sent_draft = draft.model_copy(
            update={
                "status": EmailDraftStatus.SENT,
                "sent_at": datetime.utcnow(),
                "to": recipient,
            }
        )
        await container.draft_repo.save(sent_draft)
        typer.echo(json.dumps(result.model_dump(), indent=2))

    try:
        asyncio.run(_run())
    except JobHunterError as e:
        logger.error("send-draft failed: {}", e)
        raise typer.Exit(code=2) from e


# -----------------------------------------------------------------------
# run — end-to-end pipeline (Phase 4 preview): search → score → act
# -----------------------------------------------------------------------


@app.command("run")
def run_cmd(
    portal: str = typer.Option("naukri", "--portal", help="Registered portal name."),
    query: str = typer.Option(
        "software engineer", "--query", "-q", help="Search keywords."
    ),
    location: str | None = typer.Option(None, "--location", help="Optional location filter."),
    limit: int = typer.Option(1, "--limit", min=1, max=50, help="Max jobs this run."),
    headless: bool | None = typer.Option(
        None, "--headless/--headed", help="Override BROWSER_HEADLESS for this run."
    ),
    refresh_resume: bool = typer.Option(
        False, "--refresh-resume", help="Force re-fetch resume before scoring."
    ),
) -> None:
    """One-shot pipeline run: search a portal, score every result, fire actions."""
    from jobhunter.application.run_pipeline import run_pipeline
    from jobhunter.core.entities import JobQuery

    container = build_container()
    _configure_logging(container.settings.log_level)

    if headless is not None:
        container.settings.browser_headless = headless

    logger.info(
        "run portal={} query={!r} limit={} refresh_resume={}",
        portal,
        query,
        limit,
        refresh_resume,
    )

    async def _run() -> None:
        report = await run_pipeline(
            container=container,
            portal_name=portal,
            query=JobQuery(keywords=query, location=location),
            limit=limit,
            refresh_resume=refresh_resume,
        )
        typer.echo(json.dumps(report.__dict__, indent=2, default=str))

    try:
        asyncio.run(_run())
    except JobHunterError as e:
        logger.error("run failed: {}", e)
        raise typer.Exit(code=2) from e


if __name__ == "__main__":
    app()
