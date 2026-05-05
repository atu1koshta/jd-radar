"""LLMProvider port + neutral request/response types.

Every adapter (Ollama, Anthropic, OpenAI, ...) consumes the same Prompt /
Completion / ToolSpec types. Use cases never import a vendor SDK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None


class Prompt(BaseModel):
    """Vendor-neutral prompt envelope.

    Either supply a fully-formed `messages` list, or use `system` + `user`
    for the common single-turn case.

    `extra` is a best-effort escape hatch for backend-specific knobs
    (Ollama `options.seed`, OpenAI `seed`, Anthropic `top_k`, ...). Values
    placed here are passed through to the active adapter and silently
    dropped by adapters that don't recognise them. **Portability across
    backends is NOT guaranteed.** Use it for tuning, never for correctness.
    """

    system: str | None = None
    user: str | None = None
    messages: list[Message] = Field(default_factory=list)
    temperature: float = 0.2
    max_tokens: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_messages(self) -> list[Message]:
        if self.messages:
            return list(self.messages)
        out: list[Message] = []
        if self.system:
            out.append(Message(role="system", content=self.system))
        if self.user:
            out.append(Message(role="user", content=self.user))
        return out


class Completion(BaseModel):
    text: str
    model: str
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the tool's arguments",
    )

    @classmethod
    def from_pydantic(
        cls,
        *,
        name: str,
        description: str,
        model: type[BaseModel],
    ) -> "ToolSpec":
        """Build a ToolSpec from a Pydantic model.

        Mirrors the ergonomics of `LLMProvider.structured(prompt, schema)`:
        the caller defines arguments as a Pydantic class and the JSON Schema
        is materialised here, identically across vendors.
        """
        return cls(
            name=name,
            description=description,
            parameters=model.model_json_schema(),
        )


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMCapabilities(BaseModel):
    name: str
    supports_native_tools: bool = False
    supports_json_schema: bool = False
    supports_streaming: bool = True
    max_context: int = 8192


@runtime_checkable
class LLMProvider(Protocol):
    """Contract every LLM backend must satisfy."""

    capabilities: LLMCapabilities

    async def complete(self, prompt: Prompt) -> Completion: ...

    async def structured(self, prompt: Prompt, schema: type[T]) -> T:
        """Return a Pydantic instance of `schema`.

        Adapters fall back to prompt-engineered JSON when the backend lacks
        native JSON-schema support; the caller never has to care.
        """
        ...

    async def tool_call(
        self, prompt: Prompt, tools: list[ToolSpec]
    ) -> ToolCall | Completion:
        """Invoke a tool-using turn. Returns a ToolCall when the model picked
        a tool, or a plain Completion when it answered directly."""
        ...

    async def stream(self, prompt: Prompt) -> AsyncIterator[str]: ...
