"""Watch bridge, without an engine — frame parsing, buffering, cursors, waiters.

These cover the logic that turns Montycat's live subscription frames into MCP
push, so they must run in CI where no database exists.
"""

from __future__ import annotations

import asyncio

import pytest

from memocat_mcp.watch import MemoryWatch, parse_frame


def upsert(key: str, value_json: str) -> dict:
    return {"status": True, "message": "Key inserted/updated",
            "payload": {"__key__": key, "__value__": value_json}, "error": None}


def removed(key: str) -> dict:
    return {"status": True, "message": "Key removed",
            "payload": {"__key__": key}, "error": None}


def handshake() -> dict:
    return {"status": True, "message": "Subscription started", "payload": None, "error": None}


# ── parse_frame ──────────────────────────────────────────────────────────────

def test_insert_frame_decodes_value_to_object():
    assert parse_frame(upsert("123", '{"text":"hello"}')) == {
        "key": "123", "event": "inserted", "value": {"text": "hello"}
    }


def test_remove_frame():
    assert parse_frame(removed("9")) == {"key": "9", "event": "removed", "value": None}


def test_handshake_is_not_a_change():
    assert parse_frame(handshake()) is None


@pytest.mark.parametrize("frame", ["nonsense", {}, None, {"payload": {}}])
def test_malformed_frames_ignored(frame):
    assert parse_frame(frame) is None


def test_non_json_value_passes_through():
    assert parse_frame(upsert("5", "not json"))["value"] == "not json"


# ── buffering and cursors ────────────────────────────────────────────────────

def test_buffer_is_bounded_and_seq_monotonic():
    watch = MemoryWatch("k", buffer_size=3)
    for i in range(5):
        watch._handle_frame(upsert(str(i), '{"i":%d}' % i))
    watch._handle_frame(handshake())

    assert watch.seq == 5, "handshake must not advance seq"
    assert [c["key"] for c in watch.changes] == ["2", "3", "4"], "oldest evicted"
    assert [c["seq"] for c in watch.changes] == [3, 4, 5]


def test_since_returns_only_newer():
    watch = MemoryWatch("k")
    for i in range(4):
        watch._handle_frame(upsert(str(i), "{}"))
    assert [c["seq"] for c in watch.since(2)] == [3, 4]


def test_since_none_replays_nothing():
    """A first call watches from now on; it must not replay the agent's own
    writes as if they were somebody else's news."""
    watch = MemoryWatch("k")
    watch._handle_frame(upsert("1", "{}"))
    assert watch.since(None) == []


def test_expired_cursor_is_detected_after_buffer_overflow():
    watch = MemoryWatch("k", buffer_size=2)
    for i in range(4):
        watch._handle_frame(upsert(str(i), "{}"))

    assert watch.oldest_seq == 3
    assert watch.cursor_expired(1) is True
    assert watch.cursor_expired(2) is False


# ── waiting ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buffered_changes_return_immediately():
    watch = MemoryWatch("k")
    for i in range(3):
        watch._handle_frame(upsert(str(i), "{}"))
    got = await watch.wait(since_seq=1, timeout=0.1)
    assert [c["seq"] for c in got] == [2, 3]


@pytest.mark.asyncio
async def test_timeout_returns_empty_and_cleans_up():
    watch = MemoryWatch("k")
    loop = asyncio.get_running_loop()
    start = loop.time()
    got = await watch.wait(since_seq=watch.seq, timeout=0.3)
    elapsed = loop.time() - start

    assert got == []
    assert 0.25 < elapsed < 2.0, f"timeout not honored ({elapsed:.2f}s)"
    assert watch.waiters == [], "waiter leaked after timeout"


@pytest.mark.asyncio
async def test_incoming_change_wakes_the_waiter():
    watch = MemoryWatch("k")

    async def writer():
        await asyncio.sleep(0.05)
        watch._handle_frame(upsert("77", '{"text":"pushed"}'))

    loop = asyncio.get_running_loop()
    start = loop.time()
    got, _ = await asyncio.gather(watch.wait(since_seq=None, timeout=5), writer())
    elapsed = loop.time() - start

    assert len(got) == 1 and got[0]["key"] == "77"
    assert elapsed < 1.0, "woke on timeout rather than on the change"
    assert watch.waiters == []


@pytest.mark.asyncio
async def test_captured_baseline_returns_change_that_arrives_before_waiter():
    watch = MemoryWatch("k")
    baseline = watch.seq
    watch._handle_frame(upsert("77", '{"text":"already buffered"}'))

    got = await watch.wait(since_seq=baseline, timeout=0.1)
    assert [change["key"] for change in got] == ["77"]


# ── notifier isolation ───────────────────────────────────────────────────────

def test_on_change_receives_the_record():
    seen = []
    watch = MemoryWatch("k")
    watch.on_change = lambda w, rec: seen.append(rec["key"])
    watch._handle_frame(upsert("42", "{}"))
    assert seen == ["42"]


def test_broken_notifier_does_not_kill_the_subscription():
    """The notifier pushes to MCP clients; if that throws, the engine
    subscription must keep draining or every consumer stalls."""
    watch = MemoryWatch("k")
    watch.on_change = lambda w, rec: (_ for _ in ()).throw(RuntimeError("boom"))
    watch._handle_frame(upsert("1", "{}"))
    watch._handle_frame(upsert("2", "{}"))
    assert watch.seq == 2


def test_in_use_tracks_resource_subscribers():
    watch = MemoryWatch("k")
    assert not watch.in_use()
    watch.resource_uris.add("memocat://memory/k")
    assert watch.in_use()


# ── liveness ─────────────────────────────────────────────────────────────────
# The client's `send_data` swallows connection errors and returns them as a
# plain string, so a failed subscribe completes normally. Without these, a dead
# subscription reported `timed_out: true` — a caller waited the full timeout and
# was told nothing had changed.

@pytest.mark.asyncio
async def test_finished_task_is_not_running():
    watch = MemoryWatch("k")
    watch._task = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0.01)
    assert watch._task.done()
    assert watch.running is False, "a finished reader task is a dead subscription"


@pytest.mark.asyncio
async def test_failure_surfaces_send_data_error_string():
    async def failed_connect():
        return "Error: [Errno 61] Connect call failed ('127.0.0.1', 59998)"

    watch = MemoryWatch("k")
    watch._task = asyncio.create_task(failed_connect())
    await asyncio.sleep(0.01)

    problem = watch.failure()
    assert problem is not None
    assert "Connect call failed" in problem


@pytest.mark.asyncio
async def test_ensure_established_reports_dead_subscription_fast():
    async def failed_connect():
        return "Error: connection refused"

    watch = MemoryWatch("k")
    watch._task = asyncio.create_task(failed_connect())

    loop = asyncio.get_running_loop()
    start = loop.time()
    problem = await watch.ensure_established(grace=5)
    elapsed = loop.time() - start

    assert problem is not None
    assert elapsed < 2, "must fail fast, not sit out the grace period"


@pytest.mark.asyncio
async def test_handshake_marks_the_subscription_established():
    watch = MemoryWatch("k")
    assert watch.established is False

    watch._handle_frame(handshake())
    assert watch.established is True
    assert watch.seq == 0, "the handshake is not a change"

    watch._task = asyncio.create_task(asyncio.sleep(10))
    try:
        assert await watch.ensure_established(grace=5) is None
    finally:
        watch._task.cancel()


# ── authorization leases ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revocation_purges_buffer_stops_reader_and_wakes_waiter():
    watch = MemoryWatch("private")
    watch._handle_frame(upsert("secret", '{"text":"buffered"}'))
    watch._task = asyncio.create_task(asyncio.sleep(30))
    watch._stop = asyncio.Event()
    waiter = asyncio.create_task(watch.wait(since_seq=watch.seq, timeout=30))
    await asyncio.sleep(0)

    await watch.revoke("read permission revoked")

    assert watch.revoked_error == "read permission revoked"
    assert list(watch.changes) == []
    assert watch.running is False
    assert await asyncio.wait_for(waiter, timeout=1) == []
    assert watch.waiters == []


@pytest.mark.asyncio
async def test_authorization_lease_revokes_automatically(monkeypatch):
    class Keyspace:
        @classmethod
        async def subscribe(cls, callback, subscription_port=None):
            del callback, subscription_port
            return asyncio.create_task(asyncio.sleep(30)), asyncio.Event()

    checks = 0

    async def denied():
        nonlocal checks
        checks += 1
        return "read authority no longer includes keyspace"

    monkeypatch.setenv("MONTYCAT_WATCH_AUTH_LEASE_SEC", "1")
    watch = MemoryWatch("private")
    watch._handle_frame(upsert("secret", '{"text":"buffered"}'))
    await watch.start(Keyspace, authorize=denied)

    deadline = asyncio.get_running_loop().time() + 3
    while watch.revoked_error is None and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)

    assert checks == 1
    assert watch.revoked_error is not None
    assert list(watch.changes) == []
    assert watch.running is False
    await watch.stop()


@pytest.mark.asyncio
async def test_registry_does_not_restart_a_revoked_watch():
    from memocat_mcp.watch import WatchRegistry

    class Keyspace:
        starts = 0

        @classmethod
        async def subscribe(cls, callback, subscription_port=None):
            del callback, subscription_port
            cls.starts += 1
            return asyncio.create_task(asyncio.sleep(30)), asyncio.Event()

    registry = WatchRegistry()
    watch = await registry.get_or_start("private", Keyspace)
    await watch.revoke("denied")
    same = await registry.get_or_start("private", Keyspace)

    assert same is watch
    assert Keyspace.starts == 1
    await registry.stop_all()
