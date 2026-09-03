"""Listing order against a live engine.

`montycat_list_memories(recent=True)` used to mean "read the latest storage
volume", which is only approximately recency. Engine >= 1.3.1 orders key ranges,
so on a persistent keyspace the newest record must come back first — and the
oldest-first read must be its exact reverse.
"""

from __future__ import annotations

import pytest

from .conftest import requires_engine

pytestmark = [pytest.mark.asyncio, requires_engine]

WRITES = [f"memory number {i}" for i in range(6)]


def payload(result):
    """Records from a listing. `montycat_list_memories` ends in a bulk read, so
    the payload is `{"succeeded": [...]}` rather than a bare list."""
    if isinstance(result, dict) and result.get("status") is False:
        pytest.fail(f"engine call failed: {result.get('error')}")
    body = result.get("payload") if isinstance(result, dict) else None
    if isinstance(body, dict):
        body = body.get("succeeded")
    return body if isinstance(body, list) else []


def texts(hits):
    return [hit["__value__"]["text"] for hit in hits]


@pytest.fixture
async def written(server, keyspace):
    # Sequential, not bulk: the assertion is about write order, so the writes
    # must be ordered too.
    for text in WRITES:
        await server.montycat_remember(value={"text": text}, keyspace=keyspace)
    return keyspace


async def test_recent_listing_returns_newest_first(server, written):
    hits = payload(await server.montycat_list_memories(keyspace=written, limit=6))

    assert texts(hits) == list(reversed(WRITES))


async def test_oldest_first_listing_is_the_exact_reverse(server, written):
    hits = payload(await server.montycat_list_memories(
        keyspace=written, limit=6, recent=False))

    assert texts(hits) == WRITES


async def test_limit_takes_the_newest_slice_not_an_arbitrary_one(server, written):
    """The trap in a descending range read: returning the oldest N, or N+1."""
    hits = payload(await server.montycat_list_memories(keyspace=written, limit=2))

    assert texts(hits) == list(reversed(WRITES))[:2]


async def test_limit_of_one_is_a_range_not_a_no_op(server, written):
    """A [0, 0] range reads to the client as "no range" — limit=1 must not
    collapse into a full scan."""
    hits = payload(await server.montycat_list_memories(keyspace=written, limit=1))

    assert texts(hits) == [WRITES[-1]]
