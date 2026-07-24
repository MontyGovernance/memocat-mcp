"""Per-session resource notification ownership, independent of an engine."""

from __future__ import annotations

import asyncio

import pytest

from memocat_mcp import server
from memocat_mcp.watch import MemoryWatch


URI = "memocat://memory/team"


class Session:
    def __init__(self):
        self.received = []

    async def send_resource_updated(self, uri):
        self.received.append(str(uri))


@pytest.fixture(autouse=True)
def clear_sessions():
    server._resource_sessions.clear()
    yield
    server._resource_sessions.clear()


def test_resource_session_ownership_is_per_client():
    first, second = Session(), Session()
    server._add_resource_session(URI, first)
    server._add_resource_session(URI, second)

    assert server._remove_resource_session(URI, first) is False
    assert list(server._resource_sessions[URI].values()) == [second]
    assert server._remove_resource_session(URI, second) is True
    assert URI not in server._resource_sessions


def test_revocation_discards_all_resource_session_ownership():
    first, second = Session(), Session()
    watch = MemoryWatch("team")
    watch.resource_uris.add(URI)
    server._add_resource_session(URI, first)
    server._add_resource_session(URI, second)

    server._watch_revoked(watch, "permission revoked")

    assert URI not in server._resource_sessions
    assert watch.resource_uris == set()


@pytest.mark.asyncio
async def test_notifications_fan_out_to_every_subscribed_session():
    first, second = Session(), Session()
    watch = MemoryWatch("team")
    watch.resource_uris.add(URI)
    server._add_resource_session(URI, first)
    server._add_resource_session(URI, second)

    server._notify_resource_updated(watch, {"seq": 1})
    await asyncio.sleep(0)

    assert first.received == [URI]
    assert second.received == [URI]
