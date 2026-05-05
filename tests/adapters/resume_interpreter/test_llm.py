"""LLMResumeInterpreter: schema flow, provenance stamping, error wrapping."""

from __future__ import annotations

from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from jobhunter.adapters.resume_interpreter.llm import LLMResumeInterpreter
from jobhunter.core.entities import InterpretedResume
from jobhunter.core.errors import ResumeError
from jobhunter.ports.llm import LLMCapabilities, Prompt

T = TypeVar("T", bound=BaseModel)


class FakeLLM:
    def __init__(self, payload: InterpretedResume | Exception) -> None:
        self._payload = payload
        self.last_prompt: Prompt | None = None
        self.capabilities = LLMCapabilities(
            name="fake-llm", supports_native_tools=True, supports_json_schema=True
        )

    async def complete(self, prompt: Prompt) -> Any:
        raise AssertionError("interpreter must use structured()")

    async def structured(self, prompt: Prompt, schema: type[T]) -> T:
        assert schema is InterpretedResume
        self.last_prompt = prompt
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload  # type: ignore[return-value]

    async def tool_call(self, prompt: Prompt, tools: list) -> Any:
        raise AssertionError("unused")

    async def stream(self, prompt: Prompt):  # pragma: no cover
        if False:
            yield ""


def _ir(body_hash: str = "stale-hash") -> InterpretedResume:
    return InterpretedResume(
        summary="a summary",
        total_experience_years=6.0,
        seniority_level="senior",
        body_hash=body_hash,
        model_used="some-other-model",
    )


@pytest.mark.asyncio
async def test_interprets_and_stamps_provenance() -> None:
    llm = FakeLLM(_ir(body_hash="stale-hash"))
    interp = LLMResumeInterpreter(llm)

    out = await interp.interpret(
        body={"name": "Atul"}, body_hash="fresh-hash"
    )
    # Adapter must overwrite whatever the model returned for cache fields.
    assert out.body_hash == "fresh-hash"
    assert out.model_used == "fake-llm"
    assert out.summary == "a summary"


@pytest.mark.asyncio
async def test_empty_body_raises_resume_error() -> None:
    llm = FakeLLM(_ir())
    interp = LLMResumeInterpreter(llm)
    with pytest.raises(ResumeError):
        await interp.interpret(body={}, body_hash="x")


@pytest.mark.asyncio
async def test_llm_failure_is_wrapped_as_resume_error() -> None:
    llm = FakeLLM(RuntimeError("boom"))
    interp = LLMResumeInterpreter(llm)
    with pytest.raises(ResumeError) as ei:
        await interp.interpret(body={"name": "x"}, body_hash="h")
    assert "boom" in str(ei.value)


@pytest.mark.asyncio
async def test_prompt_carries_yaml_dump_of_body() -> None:
    llm = FakeLLM(_ir())
    interp = LLMResumeInterpreter(llm)
    await interp.interpret(
        body={"name": "Atul", "headline": "Architect"},
        body_hash="h",
    )
    user = llm.last_prompt.user or ""  # type: ignore[union-attr]
    assert "name: Atul" in user
    assert "headline: Architect" in user
