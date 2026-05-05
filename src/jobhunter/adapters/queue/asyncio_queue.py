"""In-process bounded asyncio.Queue adapter for the Queue port.

Termination contract:
- A single producer calls `close()` once, after pushing the last real item.
- `close()` enqueues `_END` for every consumer that has registered via
  the worker count (we don't know how many readers there are without it,
  so the orchestrator must pass the worker count when constructing the
  queue).
- Each consumer receives one `_END`, sees `None` from `get()`, exits.
"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")

_END = object()  # sentinel; never visible to callers


class AsyncioQueueAdapter(Generic[T]):
    """Bounded `asyncio.Queue` exposing the `Queue[T]` port shape."""

    def __init__(self, *, maxsize: int, consumer_count: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if consumer_count < 1:
            raise ValueError("consumer_count must be >= 1")
        # Reserve `consumer_count` extra slots inside the underlying
        # asyncio.Queue so `close()` can always enqueue its shutdown
        # sentinels without blocking — even when the queue is currently
        # full of real items. Producer-visible backpressure is still
        # bounded by `_maxsize`: `put()` waits whenever the count of
        # *real* items is at or above that limit.
        self._maxsize = maxsize
        self._real_items = 0
        self._real_items_drained = asyncio.Event()
        self._real_items_drained.set()
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize + consumer_count)
        self._consumers = consumer_count
        self._closed = False

    async def put(self, item: T) -> None:
        if self._closed:
            raise RuntimeError("queue is closed; cannot put new items")
        # Manual backpressure on real items only — sentinel slots are
        # reserved in __init__.
        while self._real_items >= self._maxsize:
            self._real_items_drained.clear()
            await self._real_items_drained.wait()
        self._real_items += 1
        await self._q.put(item)

    async def get(self) -> T | None:
        item = await self._q.get()
        if item is _END:
            return None
        # Real item drained — release any producer waiting for room.
        self._real_items -= 1
        if self._real_items < self._maxsize:
            self._real_items_drained.set()
        return item  # type: ignore[return-value]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # One sentinel per consumer so each worker sees its own shutdown
        # signal without racing other workers.
        for _ in range(self._consumers):
            await self._q.put(_END)

    def qsize(self) -> int:
        return self._q.qsize()
