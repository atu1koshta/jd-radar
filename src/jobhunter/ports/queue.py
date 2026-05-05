"""Queue port — bounded async producer/consumer channel.

v1 implementation is in-process `asyncio.Queue` (lossy on crash). The
Protocol is shaped so that a future durable adapter (SQLite-backed,
Redis Streams) can drop in without changing the orchestrator. `close()`
is the explicit way producers signal end-of-stream — consumers should
treat a `None` returned from `get()` as the shutdown sentinel.
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class Queue(Protocol, Generic[T]):
    async def put(self, item: T) -> None:
        """Block until there's room for `item` (bounded queues apply
        backpressure on the producer)."""
        ...

    async def get(self) -> T | None:
        """Block until an item is available, or return `None` once
        `close()` has drained the queue. Consumers exit on `None`."""
        ...

    async def close(self) -> None:
        """Mark the producer side complete. After all already-enqueued
        items are consumed, every subsequent `get()` returns `None`."""
        ...

    def qsize(self) -> int: ...
