"""AsyncioQueueAdapter: termination, backpressure, multi-consumer fan-out."""

from __future__ import annotations

import asyncio

import pytest

from jobhunter.adapters.queue.asyncio_queue import AsyncioQueueAdapter


@pytest.mark.asyncio
async def test_close_signals_each_consumer_with_none() -> None:
    q: AsyncioQueueAdapter[int] = AsyncioQueueAdapter(maxsize=4, consumer_count=3)
    await q.put(1)
    await q.put(2)
    await q.close()

    seen: list[int | None] = []
    for _ in range(5):
        seen.append(await q.get())

    # Two real items, then three Nones (one per consumer).
    assert seen[0] in {1, 2}
    assert seen[1] in {1, 2}
    assert seen[2] is None
    assert seen[3] is None
    assert seen[4] is None


@pytest.mark.asyncio
async def test_close_idempotent() -> None:
    q: AsyncioQueueAdapter[int] = AsyncioQueueAdapter(maxsize=2, consumer_count=1)
    await q.close()
    await q.close()  # second call is a no-op
    assert await q.get() is None


@pytest.mark.asyncio
async def test_put_after_close_raises() -> None:
    q: AsyncioQueueAdapter[int] = AsyncioQueueAdapter(maxsize=2, consumer_count=1)
    await q.close()
    with pytest.raises(RuntimeError):
        await q.put(1)


@pytest.mark.asyncio
async def test_consumers_drain_in_parallel() -> None:
    q: AsyncioQueueAdapter[int] = AsyncioQueueAdapter(maxsize=4, consumer_count=2)

    async def producer() -> None:
        for i in range(6):
            await q.put(i)
        await q.close()

    async def consumer(out: list[int]) -> None:
        while True:
            item = await q.get()
            if item is None:
                return
            out.append(item)

    out_a: list[int] = []
    out_b: list[int] = []
    await asyncio.gather(producer(), consumer(out_a), consumer(out_b))

    # Every produced item is consumed exactly once, regardless of which
    # consumer happens to win the race for it. We don't assert fairness
    # because asyncio's cooperative scheduler can repeatedly hand work
    # to whichever consumer is already at the await point.
    assert sorted(out_a + out_b) == [0, 1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_bounded_queue_applies_backpressure() -> None:
    q: AsyncioQueueAdapter[int] = AsyncioQueueAdapter(maxsize=2, consumer_count=1)
    await q.put(1)
    await q.put(2)

    blocked = asyncio.create_task(q.put(3))
    # Without a consumer the third put hangs — verify it isn't done yet.
    await asyncio.sleep(0.05)
    assert not blocked.done()

    # Drain one item; the blocked put should now resolve.
    assert await q.get() == 1
    await asyncio.wait_for(blocked, timeout=0.5)


def test_constructor_validates_args() -> None:
    with pytest.raises(ValueError):
        AsyncioQueueAdapter(maxsize=0, consumer_count=1)
    with pytest.raises(ValueError):
        AsyncioQueueAdapter(maxsize=2, consumer_count=0)
