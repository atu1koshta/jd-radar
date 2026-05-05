"""Ollama adapter for `LLMProvider`.

Talks to a local Ollama daemon (default http://localhost:11434). The SDK is
async-friendly via `ollama.AsyncClient`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, TypeVar

from ollama import AsyncClient
from pydantic import BaseModel, ValidationError

from jobhunter.core.errors import CapabilityError, LLMError
from jobhunter.ports.llm import (
    Completion,
    LLMCapabilities,
    Prompt,
    ToolCall,
    ToolSpec,
)

if TYPE_CHECKING:
    from jobhunter.bootstrap.config import Settings

T = TypeVar("T", bound=BaseModel)


def _ollama_error(op: str, e: Exception) -> str:
    """Always include the exception class — ReadTimeout / ConnectError have
    empty `str()` and would otherwise produce useless error logs like
    `ollama structured chat failed: `."""
    msg = str(e).strip()
    cls = type(e).__name__
    return f"ollama {op} failed [{cls}]" + (f": {msg}" if msg else "")


class OllamaProvider:
    """Concrete `LLMProvider` backed by an Ollama daemon."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434",
        num_ctx: int = 16384,
        request_timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.num_ctx = num_ctx
        self._client = AsyncClient(host=base_url, timeout=request_timeout)
        self.capabilities = LLMCapabilities(
            name=f"ollama:{model}",
            supports_native_tools=True,
            supports_json_schema=True,
            supports_streaming=True,
            max_context=num_ctx,
        )

    @classmethod
    def from_settings(cls, settings: "Settings") -> "OllamaProvider":
        """Build an OllamaProvider from the active `Settings`.

        Every LLM adapter exposes this classmethod so the composition root
        in `bootstrap.container` can stay backend-agnostic — adding a new
        backend means writing its own `from_settings`, never editing the
        container.
        """
        return cls(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            num_ctx=settings.llm_num_ctx,
            request_timeout=settings.llm_request_timeout_s,
        )

    # ---- helpers -------------------------------------------------------

    def _options(self, prompt: Prompt) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "temperature": prompt.temperature,
            "num_ctx": self.num_ctx,
        }
        if prompt.max_tokens is not None:
            opts["num_predict"] = prompt.max_tokens
        opts.update(prompt.extra.get("options", {}))
        return opts

    def _messages(self, prompt: Prompt) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in prompt.to_messages()]

    # ---- LLMProvider impl ---------------------------------------------

    async def complete(self, prompt: Prompt) -> Completion:
        try:
            resp = await self._client.chat(
                model=self.model,
                messages=self._messages(prompt),
                options=self._options(prompt),
            )
        except Exception as e:  # noqa: BLE001 — wrap any vendor error
            raise LLMError(_ollama_error("complete", e)) from e

        return Completion(
            text=resp["message"]["content"],
            model=self.model,
            finish_reason=resp.get("done_reason"),
            usage={
                "prompt_tokens": resp.get("prompt_eval_count", 0),
                "completion_tokens": resp.get("eval_count", 0),
            },
        )

    async def structured(self, prompt: Prompt, schema: type[T]) -> T:
        json_schema = schema.model_json_schema()
        sys_addendum = (
            "Respond ONLY with a JSON object that conforms to this schema. "
            "Do not include prose, markdown, or code fences.\n"
            f"Schema: {json.dumps(json_schema)}"
        )
        merged = Prompt(
            system=(prompt.system + "\n\n" + sys_addendum) if prompt.system else sys_addendum,
            user=prompt.user,
            messages=prompt.messages,
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
            extra=prompt.extra,
        )
        try:
            resp = await self._client.chat(
                model=self.model,
                messages=self._messages(merged),
                format="json",
                options=self._options(merged),
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(_ollama_error("structured", e)) from e

        raw = resp["message"]["content"]
        try:
            return schema.model_validate_json(raw)
        except ValidationError as e:
            raise LLMError(
                f"ollama returned JSON that did not validate against {schema.__name__}: "
                f"{e}\nraw={raw[:500]}"
            ) from e

    async def tool_call(
        self, prompt: Prompt, tools: list[ToolSpec]
    ) -> ToolCall | Completion:
        if not tools:
            raise CapabilityError("tool_call requires at least one ToolSpec")

        ollama_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]
        try:
            resp = await self._client.chat(
                model=self.model,
                messages=self._messages(prompt),
                tools=ollama_tools,
                options=self._options(prompt),
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(_ollama_error("tool_call", e)) from e

        msg = resp["message"]
        calls = msg.get("tool_calls") or []
        if calls:
            first = calls[0]["function"]
            args = first.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            return ToolCall(name=first["name"], arguments=args)

        return Completion(
            text=msg.get("content", ""),
            model=self.model,
            finish_reason=resp.get("done_reason"),
        )

    async def stream(self, prompt: Prompt) -> AsyncIterator[str]:
        try:
            stream = await self._client.chat(
                model=self.model,
                messages=self._messages(prompt),
                options=self._options(prompt),
                stream=True,
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(_ollama_error("stream", e)) from e

        async for chunk in stream:
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                yield piece
