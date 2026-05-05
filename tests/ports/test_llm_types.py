"""Vendor-neutral types in `ports/llm.py`."""

from __future__ import annotations

from pydantic import BaseModel, Field

from jobhunter.ports.llm import Message, Prompt, ToolSpec


class _SearchArgs(BaseModel):
    keywords: str = Field(description="search terms")
    limit: int = Field(default=10, ge=1, le=100)


def test_tool_spec_from_pydantic_materialises_json_schema() -> None:
    spec = ToolSpec.from_pydantic(
        name="search_jobs",
        description="Search portal for matching jobs.",
        model=_SearchArgs,
    )
    assert spec.name == "search_jobs"
    assert spec.description == "Search portal for matching jobs."
    schema = spec.parameters
    assert schema["type"] == "object"
    assert "keywords" in schema["properties"]
    assert "limit" in schema["properties"]
    # Pydantic emits required for fields without a default
    assert schema["required"] == ["keywords"]


def test_prompt_to_messages_combines_system_and_user() -> None:
    p = Prompt(system="be terse", user="hello")
    msgs = p.to_messages()
    assert [m.role for m in msgs] == ["system", "user"]
    assert msgs[0].content == "be terse"
    assert msgs[1].content == "hello"


def test_prompt_to_messages_prefers_explicit_messages_list() -> None:
    p = Prompt(
        system="ignored",
        user="ignored",
        messages=[Message(role="user", content="real one")],
    )
    msgs = p.to_messages()
    assert len(msgs) == 1
    assert msgs[0].content == "real one"


def test_prompt_extra_is_present_but_documented_as_best_effort() -> None:
    # No assertion on behaviour here; just locks the shape so tests notice
    # if `extra` is ever removed from the contract.
    p = Prompt(extra={"options": {"seed": 42}})
    assert p.extra == {"options": {"seed": 42}}
