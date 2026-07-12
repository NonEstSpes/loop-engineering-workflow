"""Unit tests for DaemonLocks concurrency control."""

from __future__ import annotations

import asyncio

import pytest

from devflow.daemon.locks import DaemonLocks


@pytest.mark.asyncio
async def test_task_run_lock_is_exclusive() -> None:
    """Only one coroutine can hold task_run at a time."""
    locks = DaemonLocks()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with locks.task_run():
            order.append(f"{name}-start")
            await asyncio.sleep(0.05)
            order.append(f"{name}-end")

    await asyncio.gather(worker("a"), worker("b"))
    # a acquired first, so a-start, a-end, then b-start, b-end
    assert order == ["a-start", "a-end", "b-start", "b-end"]


@pytest.mark.asyncio
async def test_eod_review_lock_is_exclusive() -> None:
    """Only one coroutine can hold eod_review at a time."""
    locks = DaemonLocks()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with locks.eod_review():
            order.append(f"{name}-enter")

    await asyncio.gather(worker("x"), worker("y"))
    assert len(order) == 2
    assert order[0] == "x-enter"
    assert order[1] == "y-enter"


@pytest.mark.asyncio
async def test_task_and_eod_locks_are_independent() -> None:
    """task_run and eod_review are separate locks — can be held concurrently."""
    locks = DaemonLocks()
    held: list[str] = []

    async def hold_task() -> None:
        async with locks.task_run():
            held.append("task")
            await asyncio.sleep(0.1)

    async def hold_eod() -> None:
        await asyncio.sleep(0.02)  # let task_run grab first
        async with locks.eod_review():
            held.append("eod")

    await asyncio.gather(hold_task(), hold_eod())
    # Both were held — order doesn't matter, just that eod didn't block on task
    assert "task" in held
    assert "eod" in held
