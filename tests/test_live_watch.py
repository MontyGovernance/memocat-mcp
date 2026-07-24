"""Real-time memory watch against a live engine.

The differentiator: an agent is *told* its memory changed instead of polling.
These exercise the two-agent scenario — one side awaits, the other writes.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from .conftest import requires_engine

pytestmark = [pytest.mark.asyncio, requires_engine]


def body(result):
    return result.get("payload") if isinstance(result, dict) else {}


async def test_waiting_agent_wakes_on_another_agents_write(server, keyspace):
    async def other_agent_writes():
        await asyncio.sleep(0.3)  # let the subscription establish
        return await server.memocat_remember(
            value={"text": "agent A learned the deploy key rotated"}, keyspace=keyspace)

    start = time.perf_counter()
    awaited, written = await asyncio.gather(
        server.memocat_await_memory_change(keyspace=keyspace, timeout_sec=20),
        other_agent_writes(),
    )
    elapsed = time.perf_counter() - start
    result = body(awaited)

    assert result["timed_out"] is False
    assert elapsed < 5, f"woke on timeout, not on the write ({elapsed:.1f}s)"

    changes = result["changes"]
    assert len(changes) == 1
    assert str(changes[0]["key"]) == str(body(written))
    assert changes[0]["event"] == "inserted"
    assert "deploy key rotated" in changes[0]["value"]["text"], \
        "value must arrive decoded, not as a JSON blob"


async def test_delete_is_pushed_as_removed(server, keyspace):
    written = await server.memocat_remember(value={"text": "doomed"}, keyspace=keyspace)
    key = body(written)
    cursor = body(await server.memocat_await_memory_change(
        keyspace=keyspace, timeout_sec=10))["next_seq"]

    async def other_agent_deletes():
        await asyncio.sleep(0.3)
        return await server.memocat_forget(keyspace=keyspace, key=str(key))

    awaited, _ = await asyncio.gather(
        server.memocat_await_memory_change(
            keyspace=keyspace, timeout_sec=20, since_seq=cursor),
        other_agent_deletes(),
    )
    assert "removed" in [c["event"] for c in body(awaited)["changes"]]


async def test_changes_between_calls_are_buffered(server, keyspace):
    """Nothing is missed while no one is waiting — that is what makes the
    `since_seq` cursor trustworthy."""
    cursor = body(await server.memocat_await_memory_change(
        keyspace=keyspace, timeout_sec=2))["next_seq"]

    await server.memocat_remember(
        value={"text": "written while nobody was waiting"}, keyspace=keyspace)
    await asyncio.sleep(0.5)

    start = time.perf_counter()
    result = body(await server.memocat_await_memory_change(
        keyspace=keyspace, timeout_sec=20, since_seq=cursor))
    elapsed = time.perf_counter() - start

    assert len(result["changes"]) >= 1
    assert elapsed < 2, "buffered change should return immediately"


async def test_quiet_period_times_out_cleanly(server, keyspace):
    start = time.perf_counter()
    result = body(await server.memocat_await_memory_change(
        keyspace=keyspace, timeout_sec=2))
    elapsed = time.perf_counter() - start

    assert result["changes"] == []
    assert result["timed_out"] is True, "a quiet window is a normal outcome"
    assert 1.5 < elapsed < 8


async def test_subscription_release_does_not_deadlock_keyspace_removal(server):
    """A lingering engine subscription keeps sled subscribers alive and
    deadlocks later keyspace removal. This is the canary for that."""
    name = "memocat_t_deadlock_canary"
    raw = server._keyspace(name, persistent=True)
    await raw.remove_keyspace()
    await server.memocat_create_keyspace(keyspace=name, persistent=True)

    await server.memocat_await_memory_change(keyspace=name, timeout_sec=1)

    start = time.perf_counter()
    removed = await asyncio.wait_for(
        server.memocat_remove_keyspace(keyspace=name), timeout=15
    )
    elapsed = time.perf_counter() - start

    assert removed.get("status") is True
    assert elapsed < 10, "remove_keyspace hung — subscribers were not released"
    assert name not in server._ks_type_cache
    assert (name, True) not in server._keyspaces
    assert (name, False) not in server._keyspaces
