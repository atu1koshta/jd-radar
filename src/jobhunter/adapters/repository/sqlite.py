"""SQLite-backed `Repository[T]` using a single JSON-blob table per entity type.

Phase 0 keeps schema generic: every domain entity is stored as a JSON blob
keyed by (entity_type, id). Per-entity tables with proper columns + indexes
are introduced in Phase 4 once query patterns settle.

Async API on top of a sync SQLModel engine: queries run on a thread pool
via `asyncio.to_thread` so we don't drag aiosqlite in for v0.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, create_engine, select

T = TypeVar("T", bound=BaseModel)


class StoredEntity(SQLModel, table=True):
    """Generic JSON-blob row. Composite PK on (entity_type, id)."""

    __tablename__ = "stored_entity"

    entity_type: str = Field(primary_key=True)
    id: str = Field(primary_key=True)
    data: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


_ENGINES: dict[str, Any] = {}


def _get_engine(database_url: str) -> Any:
    if database_url not in _ENGINES:
        if database_url.startswith("sqlite:///"):
            db_path = database_url.replace("sqlite:///", "", 1)
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(database_url, echo=False)
        SQLModel.metadata.create_all(engine)
        _ENGINES[database_url] = engine
    return _ENGINES[database_url]


class SQLiteRepository(Generic[T]):
    """Concrete `Repository[T]` for any Pydantic entity."""

    def __init__(self, model: type[T], database_url: str = "sqlite:///data/jobhunter.db"):
        self._model = model
        self._entity_type = f"{model.__module__}.{model.__name__}"
        self._engine = _get_engine(database_url)

    # ---- sync helpers (run on thread pool) -----------------------------

    def _get_sync(self, id: str) -> T | None:
        with Session(self._engine) as s:
            row = s.get(StoredEntity, (self._entity_type, id))
            return self._model.model_validate_json(row.data) if row else None

    def _list_sync(self, **filter: Any) -> list[T]:
        with Session(self._engine) as s:
            stmt = select(StoredEntity).where(StoredEntity.entity_type == self._entity_type)
            rows = s.exec(stmt).all()
        out = [self._model.model_validate_json(r.data) for r in rows]
        if not filter:
            return out
        return [
            x for x in out
            if all(getattr(x, k, None) == v for k, v in filter.items())
        ]

    def _save_sync(self, entity: T) -> T:
        # Every entity in this codebase carries an `id` field by convention.
        eid = getattr(entity, "id", None)
        if not isinstance(eid, str) or not eid:
            raise ValueError(
                f"{type(entity).__name__} must expose a non-empty `id: str` attribute"
            )
        payload = entity.model_dump_json()
        with Session(self._engine) as s:
            existing = s.get(StoredEntity, (self._entity_type, eid))
            if existing:
                existing.data = payload
                existing.updated_at = datetime.utcnow()
            else:
                s.add(
                    StoredEntity(
                        entity_type=self._entity_type,
                        id=eid,
                        data=payload,
                    )
                )
            s.commit()
        return entity

    def _delete_sync(self, id: str) -> None:
        with Session(self._engine) as s:
            row = s.get(StoredEntity, (self._entity_type, id))
            if row:
                s.delete(row)
                s.commit()

    # ---- async port surface --------------------------------------------

    async def get(self, id: str) -> T | None:
        return await asyncio.to_thread(self._get_sync, id)

    async def list(self, **filter: Any) -> list[T]:
        return await asyncio.to_thread(self._list_sync, **filter)

    async def save(self, entity: T) -> T:
        return await asyncio.to_thread(self._save_sync, entity)

    async def delete(self, id: str) -> None:
        await asyncio.to_thread(self._delete_sync, id)


__all__ = ["SQLiteRepository", "StoredEntity"]
