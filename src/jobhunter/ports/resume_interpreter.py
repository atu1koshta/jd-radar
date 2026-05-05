"""ResumeInterpreter port.

Turn an arbitrary YAML resume body into a canonical `InterpretedResume`
suitable for scoring, drafting, and portal-search seeding. Implementations
typically wrap an `LLMProvider`, but a dummy / fixture-based interpreter is
useful for tests.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from jobhunter.core.entities import InterpretedResume


@runtime_checkable
class ResumeInterpreter(Protocol):
    async def interpret(
        self,
        *,
        body: dict[str, Any],
        body_hash: str,
    ) -> InterpretedResume:
        """Produce a canonical interpretation of the given resume body.

        `body_hash` is propagated onto the returned model so it can be used
        for cache-invalidation comparisons later without re-hashing.
        """
        ...
