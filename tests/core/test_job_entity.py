"""Job entity sanity + jd_content_hash helper."""

from __future__ import annotations

import pytest

from jobhunter.core.entities import Job, jd_content_hash


def test_jd_content_hash_empty_for_missing_or_blank_body() -> None:
    assert jd_content_hash(None) == ""
    assert jd_content_hash("") == ""


def test_jd_content_hash_stable_for_same_input() -> None:
    a = jd_content_hash("Senior Python Backend Engineer at Acme")
    b = jd_content_hash("Senior Python Backend Engineer at Acme")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_jd_content_hash_differs_when_content_changes() -> None:
    a = jd_content_hash("role A")
    b = jd_content_hash("role B")
    assert a != b


def test_job_default_jd_content_hash_is_empty() -> None:
    j = Job(
        id="naukri:1",
        portal="naukri",
        external_id="1",
        url="https://www.naukri.com/x",  # type: ignore[arg-type]
        title="x",
        company="y",
    )
    assert j.jd_content_hash == ""
    assert j.jd_raw is None
