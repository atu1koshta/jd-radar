"""Typed application settings sourced from `.env` + environment variables.

Use `get_settings()` instead of importing `Settings()` directly so that test
overrides via `Settings(**kwargs)` stay possible without a global cache
fight.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- LLM -----------------------------------------------------------
    llm_backend: str = "ollama"
    llm_model: str = "qwen2.5:7b-instruct-q4_K_M"
    llm_num_ctx: int = 16384
    llm_base_url: str = "http://localhost:11434"
    # Cold-start + heavy structured outputs (resume interpretation) can take
    # longer than the default httpx timeout. 600 s gives the daemon room to
    # load the model on the first call without spurious timeouts.
    llm_request_timeout_s: float = 600.0
    llm_max_tokens: int | None = None  # adapter-specific cap; None = backend default

    # Cloud LLM credentials. Each backend pulls its own via `from_settings()`.
    # None defaults are intentional — a missing key only matters if the user
    # actually selects that backend.
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_organization: str | None = None
    groq_api_key: str | None = None

    # ---- Embeddings ----------------------------------------------------
    embed_backend: str = "noop"
    embed_model: str = "BAAI/bge-small-en-v1.5"

    # ---- Notifier + email sender selection ----------------------------
    notifier_backend: str = "telegram"
    email_sender_backend: str = "log_only"

    # ---- Resume --------------------------------------------------------
    resume_loader: str = "github_yaml"
    resume_url: str = (
        "https://raw.githubusercontent.com/atu1koshta/developer-journey/main/resume.yaml"
    )
    resume_cache_path: Path = Path("data/resume_cache.yaml")
    resume_refresh_ttl_min: int = 60
    github_token: str | None = None

    # ---- Portals + actions --------------------------------------------
    # `NoDecode` keeps pydantic-settings from JSON-parsing the env value, so
    # `ENABLED_PORTALS=naukri,linkedin` works without JSON-quoting.
    enabled_portals: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["naukri"]
    )
    enabled_actions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["alert", "draft_email"]
    )

    # Browser controls (Playwright). `headless=False` is useful while the
    # user is still verifying selectors; production runs flip to True.
    browser_headless: bool = True
    browser_storage_state_dir: Path = Path("data/storage_state")
    browser_human_delay_ms_min: int = 250
    browser_human_delay_ms_max: int = 900

    # Pipeline concurrency. With a local LLM (Ollama) ~2 workers is the
    # sweet spot — Ollama serializes server-side, so more clients only
    # buy parallelism on the browser side. With cloud LLMs that handle
    # concurrent requests (Anthropic, Groq) bump to 3-4.
    pipeline_workers: int = 2
    pipeline_queue_size: int = 8

    # ---- Portal credentials -------------------------------------------
    naukri_email: str | None = None
    naukri_password: str | None = None
    naukri_base_url: str = "https://www.naukri.com"

    # ---- IMAP (OTP) ----------------------------------------------------
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str | None = None
    imap_pass: str | None = None

    # ---- Telegram ------------------------------------------------------
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    # ---- SMTP (manual approval only) ----------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_from: str | None = None

    # ---- Storage -------------------------------------------------------
    database_url: str = "sqlite:///data/jobhunter.db"

    # ---- Behavior ------------------------------------------------------
    risk_tolerance: float = 0.3
    dry_run: bool = True
    log_level: str = "INFO"

    @field_validator("enabled_portals", "enabled_actions", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # `.env` stores `ENABLED_PORTALS=naukri,linkedin` as a plain CSV
        # string. pydantic-settings would otherwise try to JSON-decode it
        # because the field is typed as list[str]. Coerce here so simple
        # env files keep working without quoting.
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
