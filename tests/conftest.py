"""Pytest fixtures shared across the suite."""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def ollama_reachable() -> bool:
    """True iff an Ollama daemon answers on the configured host:port."""
    host = os.environ.get("LLM_BASE_URL", "http://localhost:11434")
    # Strip scheme; we only care if the TCP port is open.
    netloc = host.split("://", 1)[-1]
    if ":" in netloc:
        h, p = netloc.split(":", 1)
        port = int(p.split("/", 1)[0])
    else:
        h, port = netloc, 11434
    try:
        with socket.create_connection((h, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _isolate_settings_cache() -> Iterator[None]:
    """Settings is cached via lru_cache; clear between tests so env tweaks land."""
    from jobhunter.bootstrap.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
