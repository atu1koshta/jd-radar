"""Composition root.

`Container` exposes:

* hot-path use-case dependencies as named, fully-built attributes
  (`llm`, `resume_loader`, ...);
* every other plugin group (portals, actions, notifiers, embeddings) as a
  generic `plugins` map of `{group: {name: class}}`. Use cases / orchestrator
  resolve those classes at the moment of need so that adding a new portal
  or action plugin never requires a new field on this dataclass.

`build_container()` is the single function the CLI / scheduler call. New
adapters land by registering their entry point in `pyproject.toml`; nothing
in this module changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobhunter.adapters.browser.playwright_driver import PlaywrightDriver
from jobhunter.adapters.page_extractor.trafilatura_md import TrafilaturaPageExtractor
from jobhunter.adapters.repository.sqlite import SQLiteRepository
from jobhunter.adapters.resume_interpreter.llm import LLMResumeInterpreter
from jobhunter.adapters.resume_loader.github_yaml import GitHubYamlResumeLoader
from jobhunter.bootstrap import registry
from jobhunter.bootstrap.config import Settings
from jobhunter.core.entities import (
    ActionRecord,
    Match,
    Resume,
    Run,
)
from jobhunter.core.errors import ConfigError
from jobhunter.ports.auth import Credentials
from jobhunter.ports.browser import BrowserDriver
from jobhunter.ports.llm import LLMProvider
from jobhunter.ports.notifier import NotificationChannel
from jobhunter.ports.page_extractor import PageExtractor
from jobhunter.ports.portal import PortalAdapter, PortalContext
from jobhunter.ports.repository import Repository
from jobhunter.ports.resume_interpreter import ResumeInterpreter
from jobhunter.ports.resume_loader import ResumeLoader


@dataclass
class Container:
    settings: Settings

    # Generic plugin classes discovered via entry points. Keyed by group
    # ("portals", "actions", "notifiers", "embeddings", "llm") then by the
    # entry-point name. Stores classes, not instances — instantiation is
    # the responsibility of the use case so each adapter receives only the
    # ports it actually depends on (see plan: ActionContext design).
    plugins: dict[str, dict[str, type[Any]]] = field(default_factory=dict)

    # Hot-path, fully-constructed instances. These are the deps every run
    # of the pipeline needs, so we build them eagerly rather than going
    # through the plugin map every time.
    llm: LLMProvider | None = None
    resume_repo: Repository[Resume] | None = None
    resume_interpreter: ResumeInterpreter | None = None
    resume_loader: ResumeLoader | None = None
    browser: BrowserDriver | None = None
    page_extractor: PageExtractor | None = None
    notifier: NotificationChannel | None = None
    match_repo: Repository[Match] | None = None
    action_record_repo: Repository[ActionRecord] | None = None
    run_repo: Repository[Run] | None = None

    # ---- accessors ----------------------------------------------------

    def plugin(self, group: str, name: str) -> type[Any]:
        try:
            return self.plugins[group][name]
        except KeyError as e:
            available = sorted(self.plugins.get(group, {}))
            raise ConfigError(
                f"plugin '{name}' not found in group '{group}'. "
                f"Available: {available}"
            ) from e

    def action(self, name: str) -> type[Any]:
        return self.plugin("actions", name)

    def portal(self, name: str) -> type[Any]:
        return self.plugin("portals", name)

    def notifier(self, name: str) -> type[Any]:
        return self.plugin("notifiers", name)

    def embedding(self, name: str) -> type[Any]:
        return self.plugin("embeddings", name)

    def build_action(self, name: str) -> Any:
        """Instantiate a registered action by name.

        Actions take no constructor args today (they pull deps from the
        ActionContext at execute time); a future plugin with deps would
        declare them here.
        """
        return self.action(name)()

    def build_portal(self, name: str) -> PortalAdapter:
        """Construct a PortalAdapter by name, wiring its `PortalContext`.

        The container owns the cred + selector wiring; new portals only
        need to declare their entry point and accept a `PortalContext`
        in their constructor.
        """
        if self.browser is None or self.page_extractor is None:
            raise ConfigError("browser/page_extractor not built; cannot construct portal")
        return _build_portal(name, self.settings, self.plugins, self.browser, self.page_extractor)


def _instantiate_llm(cls: type[Any], settings: Settings) -> LLMProvider:
    """Construct an LLMProvider via `from_settings(s)` if the adapter
    exposes one, otherwise fall back to a model-only constructor.

    Every backend should implement `from_settings` so it can pull its own
    config (base URLs, timeouts) without making this function know about
    every backend.
    """
    if hasattr(cls, "from_settings"):
        return cls.from_settings(settings)  # type: ignore[no-any-return]
    return cls(model=settings.llm_model)


def _build_llm(settings: Settings, plugins: dict[str, dict[str, type[Any]]]) -> LLMProvider:
    backend = settings.llm_backend.lower()
    llm_classes = plugins.get("llm", {})
    if backend not in llm_classes:
        raise ConfigError(
            f"LLM backend '{backend}' is not registered. "
            f"Available: {sorted(llm_classes)}"
        )
    return _instantiate_llm(llm_classes[backend], settings)


def _build_portal(
    name: str,
    settings: Settings,
    plugins: dict[str, dict[str, type[Any]]],
    browser: BrowserDriver,
    extractor: PageExtractor,
) -> PortalAdapter:
    """Locate the portal class via the plugin registry, build its context,
    and instantiate. Cred + auth wiring is portal-specific."""
    portal_classes = plugins.get("portals", {})
    if name not in portal_classes:
        raise ConfigError(
            f"portal '{name}' not registered. Available: {sorted(portal_classes)}"
        )
    cls = portal_classes[name]

    if name == "naukri":
        from jobhunter.adapters.portals.naukri.adapter import password_auth_for_naukri

        if not settings.naukri_email or not settings.naukri_password:
            raise ConfigError(
                "naukri requires NAUKRI_EMAIL and NAUKRI_PASSWORD in .env"
            )
        ctx = PortalContext(
            browser=browser,
            auth=password_auth_for_naukri(),
            extractor=extractor,
            credentials=Credentials(
                email=settings.naukri_email,
                password=settings.naukri_password,
            ),
            storage_state_path=str(
                settings.browser_storage_state_dir / "naukri.json"
            ),
            headless=settings.browser_headless,
        )
        return cls(ctx)

    raise ConfigError(
        f"portal '{name}' is registered but has no wiring in container._build_portal"
    )


def _build_resume_loader(
    settings: Settings,
    repo: Repository[Resume],
    interpreter: ResumeInterpreter,
) -> ResumeLoader:
    backend = settings.resume_loader.lower()
    if backend == "github_yaml":
        return GitHubYamlResumeLoader(
            url=settings.resume_url,
            repo=repo,
            cache_path=settings.resume_cache_path,
            refresh_ttl_minutes=settings.resume_refresh_ttl_min,
            interpreter=interpreter,
            github_token=settings.github_token,
        )
    raise ConfigError(f"resume_loader '{backend}' is not implemented")


def _build_notifier(
    settings: Settings, plugins: dict[str, dict[str, type[Any]]]
) -> NotificationChannel | None:
    """Build the active NotificationChannel. Returns None if creds are
    incomplete — the orchestrator skips alerts in that case rather than
    crashing the whole pipeline."""
    name = settings.notifier_backend.lower()
    classes = plugins.get("notifiers", {})
    if name not in classes:
        return None
    cls = classes[name]
    try:
        if hasattr(cls, "from_settings"):
            return cls.from_settings(settings)  # type: ignore[no-any-return]
        return cls()
    except Exception:  # noqa: BLE001
        # Missing creds, etc. Notifier stays None; logged at startup.
        return None


def build_container(settings: Settings | None = None) -> Container:
    s = settings or Settings()
    plugins = registry.all_groups()

    llm = _build_llm(s, plugins)
    resume_repo: Repository[Resume] = SQLiteRepository(Resume, database_url=s.database_url)
    interpreter: ResumeInterpreter = LLMResumeInterpreter(llm)
    resume_loader = _build_resume_loader(s, resume_repo, interpreter)
    browser = PlaywrightDriver.from_settings(s)
    page_extractor: PageExtractor = TrafilaturaPageExtractor()

    notifier = _build_notifier(s, plugins)
    match_repo: Repository[Match] = SQLiteRepository(Match, database_url=s.database_url)
    action_record_repo: Repository[ActionRecord] = SQLiteRepository(
        ActionRecord, database_url=s.database_url
    )
    run_repo: Repository[Run] = SQLiteRepository(Run, database_url=s.database_url)

    return Container(
        settings=s,
        plugins=plugins,
        llm=llm,
        resume_repo=resume_repo,
        resume_interpreter=interpreter,
        resume_loader=resume_loader,
        browser=browser,
        page_extractor=page_extractor,
        notifier=notifier,
        match_repo=match_repo,
        action_record_repo=action_record_repo,
        run_repo=run_repo,
    )
