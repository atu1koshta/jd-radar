"""Ollama adapter unit tests – no live daemon required."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel, Field

from jobhunter.adapters.llm.ollama import OllamaProvider
from jobhunter.ports.llm import Prompt

_MODEL = "qwen2.5:7b-instruct-q4_K_M"

_FAKE_STRUCTURED = {
    "title": "Senior Backend Engineer",
    "confidence": 0.75,
    "risk": 0.25,
    "reasoning": "Python/FastAPI match; Django/MySQL gap.",
}


class _Smoke(BaseModel):
    title: str
    confidence: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    reasoning: str


@pytest.fixture
def provider() -> OllamaProvider:
    return OllamaProvider(model=_MODEL, num_ctx=8192)


@pytest.mark.asyncio
async def test_structured_round_trip(provider: OllamaProvider) -> None:
    fake_resp = {"message": {"content": json.dumps(_FAKE_STRUCTURED)}}

    with patch.object(provider._client, "chat", new=AsyncMock(return_value=fake_resp)):
        result = await provider.structured(
            Prompt(
                system="Score this resume.",
                user="Score the match now.",
                temperature=0.1,
            ),
            _Smoke,
        )

    assert 0.0 <= result.confidence <= 1.0
    assert 0.0 <= result.risk <= 1.0
    assert result.title.strip()
    assert result.reasoning.strip()


def test_capabilities_advertise_native_tools_and_json(provider: OllamaProvider) -> None:
    caps = provider.capabilities
    assert caps.supports_native_tools is True
    assert caps.supports_json_schema is True
    assert caps.max_context == 8192
