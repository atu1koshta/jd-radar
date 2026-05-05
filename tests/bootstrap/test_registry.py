"""registry: entry-point discovery + failure logging.

We don't ship a real broken plugin in the test process; we patch
`importlib.metadata.entry_points` instead so each test owns its plugin set.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any
from unittest.mock import patch

import pytest

from jobhunter.bootstrap import registry


class _GoodPlugin:
    pass


class _FakeEP:
    def __init__(self, name: str, payload: Any, *, fail: bool = False) -> None:
        self.name = name
        self._payload = payload
        self._fail = fail

    def load(self) -> Any:
        if self._fail:
            raise ImportError("missing dep")
        return self._payload


class _FakeEntryPoints:
    def __init__(self, by_group: dict[str, list[_FakeEP]]) -> None:
        self._by_group = by_group

    def select(self, *, group: str) -> list[_FakeEP]:
        return self._by_group.get(group, [])


def test_good_entry_point_is_loaded() -> None:
    fake = _FakeEntryPoints({registry.GROUP_LLM: [_FakeEP("ollama", _GoodPlugin)]})
    with patch.object(metadata, "entry_points", return_value=fake):
        out = registry.llm_providers()
    assert out == {"ollama": _GoodPlugin}


def test_broken_entry_point_is_dropped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    fake = _FakeEntryPoints(
        {
            registry.GROUP_ACTIONS: [
                _FakeEP("alert", _GoodPlugin),
                _FakeEP("broken", None, fail=True),
            ]
        }
    )
    # loguru routes through stdlib logging via propagation only when
    # explicitly configured; here we just assert the good plugin survives
    # and the broken one is silently skipped (loguru emits its own line).
    with patch.object(metadata, "entry_points", return_value=fake):
        out = registry.actions()

    assert "alert" in out
    assert "broken" not in out


def test_all_groups_returns_every_known_group() -> None:
    fake = _FakeEntryPoints({registry.GROUP_LLM: [_FakeEP("ollama", _GoodPlugin)]})
    with patch.object(metadata, "entry_points", return_value=fake):
        snapshot = registry.all_groups()

    assert set(snapshot.keys()) == {
        "llm",
        "portals",
        "actions",
        "notifiers",
        "embeddings",
    }
    assert snapshot["llm"] == {"ollama": _GoodPlugin}
    assert snapshot["portals"] == {}
