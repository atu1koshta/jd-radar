"""Pipeline states. Pure enum — no behavior."""

from __future__ import annotations

from enum import StrEnum


class PipelineState(StrEnum):
    IDLE = "IDLE"
    LOGIN_CHECK = "LOGIN_CHECK"
    FETCH_OTP = "FETCH_OTP"
    AUTHENTICATED = "AUTHENTICATED"
    SEARCH = "SEARCH"
    COLLECT_JOBS = "COLLECT_JOBS"
    EXTRACT_JD = "EXTRACT_JD"
    SCORE = "SCORE"
    DECIDE_ACTIONS = "DECIDE_ACTIONS"
    EXECUTE_ACTIONS = "EXECUTE_ACTIONS"
    PERSIST = "PERSIST"
    SLEEP = "SLEEP"
    HALTED = "HALTED"
