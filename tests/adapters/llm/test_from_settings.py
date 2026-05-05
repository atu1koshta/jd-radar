"""OllamaProvider.from_settings unit test (no daemon required)."""

from __future__ import annotations

from pathlib import Path

from jobhunter.adapters.llm.ollama import OllamaProvider
from jobhunter.bootstrap.config import Settings


def test_from_settings_propagates_llm_fields(tmp_path: Path) -> None:
    s = Settings(
        llm_backend="ollama",
        llm_model="qwen2.5:3b-instruct-q4_K_M",
        llm_base_url="http://localhost:11434",
        llm_num_ctx=4096,
        llm_request_timeout_s=42.0,
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        resume_cache_path=tmp_path / "c.yaml",
    )

    provider = OllamaProvider.from_settings(s)
    assert provider.model == "qwen2.5:3b-instruct-q4_K_M"
    assert provider.num_ctx == 4096
    assert provider.capabilities.max_context == 4096
    assert provider.capabilities.name == "ollama:qwen2.5:3b-instruct-q4_K_M"
