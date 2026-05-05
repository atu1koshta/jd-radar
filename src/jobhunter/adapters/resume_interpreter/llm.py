"""LLM-backed `ResumeInterpreter`.

One call per resume *content* version. Result is cached on the Resume row
and reused across every job in the pipeline until the YAML body changes.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from typing import Any

import yaml

from jobhunter.core.entities import InterpretedResume
from jobhunter.core.errors import ResumeError
from jobhunter.ports.llm import LLMProvider, Prompt


_SYSTEM_PROMPT = textwrap.dedent(
    """\
    You are a career-data interpreter for a job-hunting assistant.

    You will be given a candidate's resume, expressed as YAML. The keys can
    vary between candidates — be tolerant of unknown shapes and infer.

    Produce a strict JSON object that matches the schema you are given.
    Do not include prose, comments, or markdown.

    Rules:
    - Do not invent companies, titles, or skills. If the resume does not
      mention something, leave it out or set it to null.
    - Categorise each skill: language / framework / database / cloud /
      devops / data / ml / frontend / mobile / tooling / soft / other.
    - For each experience, infer `seniority` from the title (junior, mid,
      senior, staff, principal, lead). Use null if ambiguous.
    - Compute `duration_years` from start/end fields (use today for
      currently-held roles). Sum these into `total_experience_years`,
      avoiding overlaps when in doubt.
    - `seniority_level` of the candidate overall = the seniority of the
      latest non-intern role.
    - `summary` is 2-3 sentences max, third-person, plain prose, no fluff.
    - `domains` are industry tags from past employers (fintech, healthcare,
      e-commerce, gaming, ...). Empty list is fine.
    - `role_categories` are high-level buckets: backend, frontend, fullstack,
      mobile, ml, data, devops, sre, qa, security, embedded, lead.
    - `search_query_terms` is 5-10 short keyword phrases the assistant should
      use to look for matching jobs (e.g. "senior python backend engineer",
      "fastapi postgres aws", "platform engineer fintech").
    """
).strip()


class LLMResumeInterpreter:
    """Concrete `ResumeInterpreter` that delegates schema-shaping to the LLM."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def interpret(
        self,
        *,
        body: dict[str, Any],
        body_hash: str,
    ) -> InterpretedResume:
        if not body:
            raise ResumeError("cannot interpret an empty resume body")

        yaml_text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True).strip()
        prompt = Prompt(
            system=_SYSTEM_PROMPT,
            user=f"=== RESUME (YAML) ===\n{yaml_text}",
            temperature=0.1,
        )

        try:
            interpreted = await self._llm.structured(prompt, InterpretedResume)
        except Exception as e:  # noqa: BLE001
            raise ResumeError(f"resume interpretation failed: {e}") from e

        # Authoritatively stamp provenance regardless of what the model
        # returned, so the cache invalidation key is always correct.
        return interpreted.model_copy(
            update={
                "body_hash": body_hash,
                "interpreted_at": datetime.utcnow(),
                "model_used": self._llm.capabilities.name,
            }
        )
