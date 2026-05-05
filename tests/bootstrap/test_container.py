"""Container wiring: from_settings dispatch, plugin map, plugin lookups."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from jobhunter.bootstrap import container as container_mod
from jobhunter.bootstrap.config import Settings
from jobhunter.core.errors import ConfigError


class _StubLLM:
    def __init__(self, *, model: str, base_url: str, num_ctx: int) -> None:
        self.model = model
        self.base_url = base_url
        self.num_ctx = num_ctx
        self.capabilities = type(
            "C",
            (),
            {
                "name": f"stub:{model}",
                "supports_native_tools": True,
                "supports_json_schema": True,
                "supports_streaming": True,
                "max_context": num_ctx,
            },
        )()

    @classmethod
    def from_settings(cls, settings: Settings) -> "_StubLLM":
        return cls(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            num_ctx=settings.llm_num_ctx,
        )


class _LegacyLLM:
    """Adapter that has no `from_settings` — must still construct via fallback."""

    def __init__(self, *, model: str) -> None:
        self.model = model
        self.capabilities = type(
            "C",
            (),
            {
                "name": f"legacy:{model}",
                "supports_native_tools": False,
                "supports_json_schema": False,
                "supports_streaming": False,
                "max_context": 4096,
            },
        )()


class _StubAction:
    pass


class _StubPortal:
    pass


class _StubEmailSender:
    name = "log_only"

    @classmethod
    def from_settings(cls, _settings: Settings) -> "_StubEmailSender":
        return cls()


@pytest.fixture
def fake_settings(tmp_path) -> Settings:
    return Settings(
        llm_backend="stub",
        llm_model="stub-model",
        llm_base_url="http://localhost:11434",
        llm_num_ctx=8192,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        resume_url="https://example.test/resume.yaml",
        resume_cache_path=tmp_path / "resume_cache.yaml",
        enabled_portals=["naukri"],
        enabled_actions=["alert"],
    )


def test_from_settings_dispatch_picks_active_backend(fake_settings: Settings) -> None:
    plugins = {
        "llm": {"stub": _StubLLM, "legacy": _LegacyLLM},
        "portals": {},
        "actions": {},
        "notifiers": {},
        "embeddings": {},
        "email_senders": {"log_only": _StubEmailSender},
    }
    with patch.object(container_mod.registry, "all_groups", return_value=plugins):
        c = container_mod.build_container(fake_settings)

    assert isinstance(c.llm, _StubLLM)
    assert c.llm.model == "stub-model"
    assert c.llm.num_ctx == 8192


def test_legacy_adapter_without_from_settings_still_constructs(fake_settings: Settings) -> None:
    fake_settings = fake_settings.model_copy(update={"llm_backend": "legacy"})
    plugins = {
        "llm": {"legacy": _LegacyLLM},
        "portals": {},
        "actions": {},
        "notifiers": {},
        "embeddings": {},
        "email_senders": {"log_only": _StubEmailSender},
    }
    with patch.object(container_mod.registry, "all_groups", return_value=plugins):
        c = container_mod.build_container(fake_settings)

    assert isinstance(c.llm, _LegacyLLM)
    assert c.llm.model == "stub-model"


def test_unknown_backend_raises_config_error(fake_settings: Settings) -> None:
    fake_settings = fake_settings.model_copy(update={"llm_backend": "made-up"})
    plugins = {
        "llm": {"stub": _StubLLM},
        "portals": {},
        "actions": {},
        "notifiers": {},
        "embeddings": {},
        "email_senders": {"log_only": _StubEmailSender},
    }
    with patch.object(container_mod.registry, "all_groups", return_value=plugins):
        with pytest.raises(ConfigError) as ei:
            container_mod.build_container(fake_settings)
    assert "made-up" in str(ei.value)


def test_plugin_lookup_helpers_return_classes(fake_settings: Settings) -> None:
    plugins: dict[str, dict[str, type[Any]]] = {
        "llm": {"stub": _StubLLM},
        "portals": {"naukri": _StubPortal},
        "actions": {"alert": _StubAction},
        "notifiers": {},
        "embeddings": {},
        "email_senders": {"log_only": _StubEmailSender},
    }
    with patch.object(container_mod.registry, "all_groups", return_value=plugins):
        c = container_mod.build_container(fake_settings)

    assert c.action("alert") is _StubAction
    assert c.portal("naukri") is _StubPortal


def test_plugin_lookup_missing_raises_config_error(fake_settings: Settings) -> None:
    plugins: dict[str, dict[str, type[Any]]] = {
        "llm": {"stub": _StubLLM},
        "portals": {},
        "actions": {"alert": _StubAction},
        "notifiers": {},
        "embeddings": {},
        "email_senders": {"log_only": _StubEmailSender},
    }
    with patch.object(container_mod.registry, "all_groups", return_value=plugins):
        c = container_mod.build_container(fake_settings)

    with pytest.raises(ConfigError) as ei:
        c.action("not_registered")
    assert "actions" in str(ei.value)
    assert "alert" in str(ei.value)
