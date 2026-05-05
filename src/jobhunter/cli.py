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


if __name__ == "__main__":
    app()
