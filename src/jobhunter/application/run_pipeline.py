"""Concurrent end-to-end orchestrator.

Pipeline shape:

    [search worker x 1]  ->  [JobQueue (bounded)]  ->  [process worker x N]

The single search worker is the producer (one browser nav at a time;
Naukri's per-account rate limits make multi-search counter-productive).
N process workers consume from the queue and run the rest of the loop
(`fetch_jd -> score -> decide_actions -> execute_actions`) in parallel.

Why N parallel `process` workers help with a local LLM:
- Worker A is in `fetch_jd` (browser idle for the LLM)
- Worker B is in `score` (Ollama busy)
- Worker C is in `execute_actions` (Telegram + DB)

Three concurrent in-flight jobs hide the per-stage latency behind the
others. Ollama still serializes calls server-side, so going beyond 2-3
buys nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from jobhunter.adapters.queue.asyncio_queue import AsyncioQueueAdapter
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
    Run,
    RunCounters,
    RunStatus,
)
from jobhunter.core.errors import ConfigError, JobHunterError
from jobhunter.ports.action import Action, ActionContext
from jobhunter.ports.queue import Queue


@dataclass
class PipelineRunReport:
    run_id: str
    portal: str
    query: str
    workers: int
    counters: RunCounters
    status: RunStatus
    error: str | None = None


async def run_pipeline(
    *,
    container: Container,
    portal_name: str,
    query: JobQuery,
    limit: int = 5,
    refresh_resume: bool = False,
    workers: int | None = None,
) -> PipelineRunReport:
    """Concurrent run: 1 search producer + N process consumers.

    `workers` defaults to `Settings.pipeline_workers`. Pass `1` for a
    purely sequential run (useful for debugging selector drift).
    """
    if container.resume_loader is None or container.llm is None:
        raise ConfigError(
            "container missing resume_loader / llm — call build_container() first"
        )

    n_workers = max(1, workers or container.settings.pipeline_workers)
    queue_size = max(n_workers, container.settings.pipeline_queue_size)

    run = Run(
        id=f"run:{uuid.uuid4().hex[:12]}",
        portal=portal_name,
        query=query.keywords,
        workers=n_workers,
    )
    if container.run_repo is not None:
        await container.run_repo.save(run)

    counters = run.counters

    resume: Resume = await load_resume(
        container.resume_loader, force_refresh=refresh_resume
    )
    if resume.interpreted is None:
        raise ConfigError(
            "resume.interpreted is None after load — run `jobhunter load-resume --refresh`"
        )
    logger.info(
        "pipeline: resume loaded ({} skills, seniority={})",
        len(resume.interpreted.skills),
        resume.interpreted.seniority_level,
    )

    actions = _instantiate_enabled_actions(container)
    logger.info(
        "pipeline: workers={} queue_size={} actions={}",
        n_workers,
        queue_size,
        [a.name for a in actions],
    )

    portal = container.build_portal(portal_name)
    job_queue: Queue[Job] = AsyncioQueueAdapter(
        maxsize=queue_size, consumer_count=n_workers
    )
    counters_lock = asyncio.Lock()

    try:
        producer = asyncio.create_task(
            _search_producer(portal=portal, query=query, limit=limit, q=job_queue)
        )
        consumers = [
            asyncio.create_task(
                _process_consumer(
                    worker_id=i,
                    container=container,
                    portal=portal,
                    resume=resume,
                    actions=actions,
                    q=job_queue,
                    counters=counters,
                    counters_lock=counters_lock,
                )
            )
            for i in range(n_workers)
        ]
        await asyncio.gather(producer, *consumers)
        run.status = RunStatus.DONE
    except JobHunterError as e:
        run.status = RunStatus.FAILED
        run.error = f"{type(e).__name__}: {e}"
        logger.error("pipeline failed: {}", run.error)
    except Exception as e:  # noqa: BLE001
        run.status = RunStatus.FAILED
        run.error = f"{type(e).__name__}: {e}"
        logger.exception("pipeline crashed: {}", e)
    finally:
        await portal.close()
        run.ended_at = datetime.utcnow()
        run.counters = counters
        if container.run_repo is not None:
            await container.run_repo.save(run)

    return PipelineRunReport(
        run_id=run.id,
        portal=portal_name,
        query=query.keywords,
        workers=n_workers,
        counters=counters,
        status=run.status,
        error=run.error,
    )


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


async def _search_producer(
    *, portal: Any, query: JobQuery, limit: int, q: Queue[Job]
) -> None:
    """Single-producer search loop. Always closes the queue, even on error."""
    try:
        async for job in portal.search(query, limit=limit):
            await q.put(job)
    finally:
        await q.close()


async def _process_consumer(
    *,
    worker_id: int,
    container: Container,
    portal: Any,
    resume: Resume,
    actions: list[Action],
    q: Queue[Job],
    counters: RunCounters,
    counters_lock: asyncio.Lock,
) -> None:
    """One worker = one in-flight job at a time. fetch_jd -> score -> act."""
    while True:
        job = await q.get()
        if job is None:
            return
        try:
            async with counters_lock:
                counters.jobs_seen += 1

            job = await portal.fetch_jd(job)
            match = await _score_and_persist(
                container=container, resume=resume, job=job
            )
            async with counters_lock:
                counters.jobs_scored += 1
                _bump_decision_counter(counters, match.decision)

            results = await _act_on_match(
                container=container,
                resume=resume,
                job=job,
                match=match,
                actions=actions,
            )
            async with counters_lock:
                counters.actions_succeeded += sum(
                    1 for r in results if str(r.outcome) == "success"
                )
                counters.actions_failed += sum(
                    1 for r in results if str(r.outcome) == "failed"
                )
        except JobHunterError as e:
            async with counters_lock:
                counters.jobs_failed += 1
            logger.warning(
                "worker {}: job {} failed: {}", worker_id, job.id, e
            )
        except Exception as e:  # noqa: BLE001
            async with counters_lock:
                counters.jobs_failed += 1
            logger.warning(
                "worker {}: job {} raised {}: {}",
                worker_id,
                job.id,
                type(e).__name__,
                e,
            )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _instantiate_enabled_actions(container: Container) -> list[Action]:
    enabled = container.settings.enabled_actions
    out: list[Action] = []
    for name in enabled:
        try:
            out.append(container.build_action(name))
        except Exception as e:  # noqa: BLE001
            logger.warning("pipeline: action '{}' could not be built ({})", name, e)
    return out


def _bump_decision_counter(counters: RunCounters, decision: Decision) -> None:
    if decision == Decision.SKIP:
        counters.matches_skipped += 1
    elif decision == Decision.ALERT:
        counters.matches_alerted += 1


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
        return []

    ctx = ActionContext(
        job=job,
        match=match,
        ports={
            "llm": container.llm,
            "notifier": container.notifier,
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
