"""Shared fixtures.

Tests split in two tiers:

* `test_watch_unit.py` and `test_stamp_unit.py` need no engine and run anywhere,
  including CI.
* `test_live_*.py` need a reachable Montycat Semantic engine and skip cleanly
  when there isn't one, so `pytest` is never red just because nothing is running.
"""

from __future__ import annotations

import os
import socket
import uuid

import pytest

DEFAULT_URI = "montycat://EUGENE:12345@127.0.0.1:21210/playground_store"
MONTYCAT_URI = os.environ.get("MONTYCAT_TEST_URI", DEFAULT_URI)


def _engine_reachable(uri: str) -> bool:
    """True if something is listening on the engine's host/port."""
    try:
        hostport = uri.split("@", 1)[1].split("/", 1)[0]
        host, port = hostport.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=1.5):
            return True
    except Exception:
        return False


requires_engine = pytest.mark.skipif(
    not _engine_reachable(MONTYCAT_URI),
    reason=f"no Montycat engine reachable at {MONTYCAT_URI}",
)


@pytest.fixture(scope="session", autouse=True)
def _configure_env():
    """Point the server module at the test engine before it is imported."""
    os.environ.setdefault("MONTYCAT_URI", MONTYCAT_URI)
    os.environ.setdefault("MONTYCAT_DEFAULT_KEYSPACE", "memocat_tests")


@pytest.fixture
def server(_configure_env):
    from memocat_mcp import server as srv

    # Server state lives at module scope in production, but pytest-asyncio gives
    # tests separate event loops. Never carry a bootstrap task or lock from one
    # test's closed loop into the next test.
    task = srv._bootstrap_task
    if task is not None and not task.done():
        task.cancel()
    srv._bootstrap_task = None
    srv._bootstrap_failure = None
    srv._bootstrap_failed_at = 0.0
    srv._acquisition_lock = None
    srv._engine = None
    srv._keyspaces.clear()
    srv._ks_type_cache.clear()

    yield srv

    task = srv._bootstrap_task
    if task is not None and not task.done():
        task.cancel()
    srv._bootstrap_task = None
    srv._bootstrap_failure = None
    srv._bootstrap_failed_at = 0.0
    srv._acquisition_lock = None
    srv._engine = None
    srv._keyspaces.clear()
    srv._ks_type_cache.clear()


@pytest.fixture
async def keyspace(server):
    """A freshly created, uniquely named persistent keyspace, dropped after.

    Unique per test so a crashed run can never poison the next one, and so
    tests may run against a shared dev engine without colliding.
    """
    name = f"memocat_t_{uuid.uuid4().hex[:10]}"
    raw = server._keyspace(name, persistent=True)
    # Tests using this shared fixture exercise semantic recall. Be explicit:
    # memocat_create_keyspace defaults to semantic=False by API contract.
    await server.memocat_create_keyspace(
        keyspace=name, persistent=True, semantic=True
    )
    try:
        yield name
    finally:
        from memocat_mcp.watch import registry

        await registry.stop_all()  # a live subscription blocks keyspace removal
        await raw.remove_keyspace()
