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

from jobhunter.adapters.repository.sqlite import SQLiteRepository
from jobhunter.adapters.resume_interpreter.llm import LLMResumeInterpreter
from jobhunter.adapters.resume_loader.github_yaml import GitHubYamlResumeLoader
from jobhunter.bootstrap import registry
from jobhunter.bootstrap.config import Settings
from jobhunter.core.entities import Resume
from jobhunter.core.errors import ConfigError
from jobhunter.ports.llm import LLMProvider
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


def _instantiate_llm(cls: type[Any], settings: Settings) -> LLMProvider:
    """Construct an LLMProvider via `from_settings(s)` if the adapter
    exposes one, otherwise fall back to a model-only constructor.

    Every cloud / local backend should implement `from_settings` so it can
    pull its own creds (api keys, base URLs, timeouts) without making this
    function know about every backend.
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


def build_container(settings: Settings | None = None) -> Container:
    s = settings or Settings()
    plugins = registry.all_groups()

    llm = _build_llm(s, plugins)
    resume_repo: Repository[Resume] = SQLiteRepository(Resume, database_url=s.database_url)
    interpreter: ResumeInterpreter = LLMResumeInterpreter(llm)
    resume_loader = _build_resume_loader(s, resume_repo, interpreter)

    return Container(
        settings=s,
        plugins=plugins,
        llm=llm,
        resume_repo=resume_repo,
        resume_interpreter=interpreter,
        resume_loader=resume_loader,
    )
