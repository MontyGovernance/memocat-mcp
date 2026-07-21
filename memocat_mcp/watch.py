"""Real-time memory watch — the bridge between Montycat's live subscriptions
and MCP push (SEMANTIC/MCP PLAN.md §7.3).

Montycat has native live subscriptions: the engine holds a connection open and
pushes every insert/update/delete on a keyspace as it happens. That is the
capability competing memory servers don't have, so agents elsewhere must poll.
Here one subscription per watched keyspace feeds two surfaces:

  * `resources/updated` notifications, for MCP clients that implement resource
    subscriptions (spec-correct, but thin on the ground today), and
  * a long-poll tool, which returns the instant a change lands and therefore
    works in every client right now.

Both read the same buffer, so a change is delivered once and seen by both.

Lifecycle matters more than it looks. An engine subscription that is never
closed keeps the server's sled subscribers alive, and that **deadlocks any
later `remove_keyspace`/`remove_store` on the same store** (see the `finally`
block in the client's `send_data`). Every exit path here — idle reap,
unsubscribe, shutdown — must set the stop event and await the task.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from typing import Any, Callable, Optional

# The engine's own wording for the two change events, plus the handshake frame
# it sends once when a subscription is established (which is not a change).
_EVENT_UPSERT = "Key inserted/updated"
_EVENT_REMOVED = "Key removed"
_HANDSHAKE = "Subscription started"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def parse_frame(frame: Any) -> Optional[dict]:
    """Turn one subscription frame into a change record, or None to ignore it.

    The engine sends (`src/subscribition_server/connection.rs`):
        {"message": "Key inserted/updated", "payload": {"__key__": .., "__value__": ..}}
        {"message": "Key removed",          "payload": {"__key__": ..}}
    preceded by a one-off "Subscription started" handshake. `__value__` is a
    JSON string; it is decoded here so the agent gets an object, not a blob.
    """
    if not isinstance(frame, dict):
        return None

    message = frame.get("message") or ""
    if _HANDSHAKE in str(message):
        return None

    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return None

    key = payload.get("__key__")
    if key is None:
        return None

    if _EVENT_REMOVED in str(message):
        return {"key": str(key), "event": "removed", "value": None}

    raw_value = payload.get("__value__")
    value: Any = raw_value
    if isinstance(raw_value, str):
        try:
            value = json.loads(raw_value)
        except (ValueError, TypeError):
            value = raw_value  # not JSON — hand back what the engine sent

    return {"key": str(key), "event": "inserted", "value": value}


class MemoryWatch:
    """One live subscription to one keyspace, fanned out to many consumers."""

    def __init__(self, keyspace: str, buffer_size: Optional[int] = None):
        self.keyspace = keyspace
        self.changes: deque = deque(maxlen=buffer_size or _env_int("MONTYCAT_WATCH_BUFFER", 500))
        self.seq = 0
        self.waiters: list[asyncio.Future] = []
        self.resource_uris: set[str] = set()
        self.last_used = time.monotonic()

        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        # Set when the engine's "Subscription started" handshake arrives. Until
        # then the subscription is not proven live, and a caller that waits on
        # it would sit through its whole timeout learning nothing.
        self.established = False
        # Called with the change record whenever one arrives; the server wires
        # this to `session.send_resource_updated`. Kept as a plain callable so
        # this module never imports the MCP SDK.
        self.on_change: Optional[Callable[["MemoryWatch", dict], None]] = None

    # ── engine side ──────────────────────────────────────────────────────────

    def _handle_frame(self, frame: Any) -> None:
        """Subscription callback. Runs **inline in the client's read loop**, so
        it must never block: anything slow here stalls delivery for every
        consumer of this keyspace."""
        if isinstance(frame, dict) and _HANDSHAKE in str(frame.get("message") or ""):
            self.established = True
            return

        change = parse_frame(frame)
        if change is None:
            return

        self.seq += 1
        record = {"seq": self.seq, **change}
        self.changes.append(record)

        for waiter in self.waiters:
            if not waiter.done():
                waiter.set_result(None)
        self.waiters.clear()

        if self.on_change is not None:
            try:
                self.on_change(self, record)
            except Exception:
                pass  # a broken notifier must not kill the subscription

    async def start(self, keyspace_cls) -> None:
        if self._task is not None:
            return
        port = _env_int("MONTYCAT_SUBSCRIPTION_PORT", 0) or None
        self._task, self._stop = await keyspace_cls.subscribe(
            callback=self._handle_frame, subscription_port=port
        )

    async def stop(self) -> None:
        """Release the engine subscription. Not optional — see module docstring:
        a lingering subscriber deadlocks later keyspace removal."""
        self.established = False
        if self._stop is not None:
            self._stop.set()
        task = self._task
        self._task = None
        self._stop = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for waiter in self.waiters:
            if not waiter.done():
                waiter.set_result(None)
        self.waiters.clear()

    @property
    def running(self) -> bool:
        """Live means the reader task is still going. A task that has *finished*
        is a dead subscription, not a running one — the client's `send_data`
        swallows connection errors and returns them as a plain string, so a
        failed subscribe completes normally and looks identical to success."""
        return self._task is not None and not self._task.done()

    def failure(self) -> Optional[str]:
        """Why the subscription is not alive, or None if it is fine.

        Without this, an unreachable subscription port produced a perfectly
        calm `timed_out: true` — the caller waited the full timeout and was told
        nothing had changed, which was a lie.
        """
        task = self._task
        if task is None:
            return "subscription was never started"
        if not task.done():
            return None
        try:
            outcome = task.result()
        except asyncio.CancelledError:
            return "subscription was cancelled"
        except Exception as exc:  # noqa: BLE001 - reported, not handled
            return f"subscription failed: {exc}"
        # `send_data` reports connection problems as a string rather than raising.
        if isinstance(outcome, str) and outcome.startswith("Error:"):
            return f"subscription failed: {outcome[len('Error:'):].strip()}"
        return "subscription closed unexpectedly"

    async def ensure_established(self, grace: float = 2.0) -> Optional[str]:
        """Give the handshake a moment to arrive; return an error if it can't.

        Returns None once the engine has confirmed the subscription (or while it
        is still plausibly connecting), and a message when it is provably dead.
        """
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if self.established:
                return None
            if self._task is not None and self._task.done():
                return self.failure()
            await asyncio.sleep(0.05)
        return None if self.running else self.failure()

    # ── consumer side ────────────────────────────────────────────────────────

    def since(self, seq: Optional[int]) -> list[dict]:
        """Buffered changes newer than `seq`. `None` means "only what happens
        from now on", so a first call doesn't replay history the agent has
        already seen in its own writes."""
        if seq is None:
            return []
        return [c for c in self.changes if c["seq"] > seq]

    @property
    def oldest_seq(self) -> int:
        """The earliest cursor still fully represented by this buffer.

        A caller with ``since_seq < oldest_seq - 1`` has missed at least one
        change. Exposing that fact lets it resync rather than treating a partial
        replay as complete history.
        """
        return self.changes[0]["seq"] if self.changes else self.seq + 1

    def cursor_expired(self, seq: Optional[int]) -> bool:
        return seq is not None and seq < self.oldest_seq - 1

    async def wait(self, since_seq: Optional[int], timeout: float) -> list[dict]:
        """Return changes newer than `since_seq`, waiting up to `timeout`
        seconds for one to arrive. Push-backed: this wakes the moment a write
        lands, it does not poll the database."""
        self.last_used = time.monotonic()

        # Direct callers may omit a cursor too; make their boundary the moment
        # wait starts, matching the MCP tool's captured baseline behavior.
        cursor = self.seq if since_seq is None else since_seq
        buffered = self.since(cursor)
        if buffered:
            return buffered

        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self.waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError:
            return []
        finally:
            self.last_used = time.monotonic()
            if waiter in self.waiters:
                self.waiters.remove(waiter)

        # Returning through ``since`` also works for a first call: the tool
        # captures its baseline sequence before registering the waiter.
        return self.since(cursor)

    def idle_for(self) -> float:
        return time.monotonic() - self.last_used

    def in_use(self) -> bool:
        return bool(self.waiters or self.resource_uris)


class WatchRegistry:
    """All active watches, keyed by keyspace name."""

    def __init__(self):
        self._watches: dict[str, MemoryWatch] = {}
        self.on_change: Optional[Callable[[MemoryWatch, dict], None]] = None

    async def get_or_start(self, keyspace: str, keyspace_cls) -> MemoryWatch:
        watch = self._watches.get(keyspace)
        if watch is None:
            watch = MemoryWatch(keyspace)
            watch.on_change = self.on_change
            self._watches[keyspace] = watch
        if not watch.running:
            await watch.start(keyspace_cls)
        return watch

    def get(self, keyspace: str) -> Optional[MemoryWatch]:
        return self._watches.get(keyspace)

    def for_uri(self, uri: str) -> Optional[MemoryWatch]:
        for watch in self._watches.values():
            if uri in watch.resource_uris:
                return watch
        return None

    async def stop(self, keyspace: str) -> None:
        watch = self._watches.pop(keyspace, None)
        if watch is not None:
            await watch.stop()

    async def stop_all(self) -> None:
        for keyspace in list(self._watches):
            await self.stop(keyspace)

    async def reap_idle(self) -> int:
        """Close subscriptions nobody is using. Keeps the connection count
        proportional to actual demand, and releases engine-side subscribers."""
        timeout = _env_int("MONTYCAT_WATCH_IDLE_TIMEOUT", 300)
        stopped = 0
        for keyspace, watch in list(self._watches.items()):
            if not watch.in_use() and watch.idle_for() > timeout:
                await self.stop(keyspace)
                stopped += 1
        return stopped


registry = WatchRegistry()
