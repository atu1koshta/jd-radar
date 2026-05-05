"""Generic Repository port.

`T` is a domain entity (Pydantic model). Adapters are responsible for
serialising it to whatever store they back onto. Use cases never see a
SQL row, ORM session, or HTTP client.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class Repository(Protocol[T]):
    async def get(self, id: str) -> T | None: ...

    async def list(self, **filter: Any) -> list[T]: ...

    async def save(self, entity: T) -> T: ...

    async def delete(self, id: str) -> None: ...
