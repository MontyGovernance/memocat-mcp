"""Engine acquisition runs behind the open transport, never in front of it.

Starting an engine can pull a container image or download an embedding model.
Awaiting that before `stdio_server()` opens would hold up `initialize` for
minutes, and no MCP client waits that long — the extension simply appears to
fail. These tests pin the ordering, the bounded wait a tool performs instead,
and the messages a caller gets while the engine is not yet usable.

No engine, no network, no Docker.
"""

from __future__ import annotations

import asyncio

import pytest

from memocat_mcp import bootstrap, server as srv


@pytest.fixture(autouse=True)
def reset_bootstrap_state(monkeypatch):
    """Each test starts with no bootstrap in flight and no cached failure."""
    monkeypatch.setattr(srv, "_bootstrap_task", None, raising=False)
    monkeypatch.setattr(srv, "_bootstrap_failure", None, raising=False)
    monkeypatch.setattr(srv, "_bootstrap_failed_at", 0.0, raising=False)
    monkeypatch.setattr(srv, "_acquisition_lock", None, raising=False)
    monkeypatch.setattr(srv, "_engine", None, raising=False)
    yield
    task = srv._bootstrap_task
    if task is not None and not task.done():
        task.cancel()


async def test_a_pending_bootstrap_reports_progress_instead_of_hanging(monkeypatch):
    started = asyncio.Event()

    async def never_finishes():
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(srv, "_bootstrap", never_finishes)
    monkeypatch.setenv("MEMOCAT_READY_TIMEOUT", "0.1")

    result = await srv.memocat_list_keyspaces()

    assert started.is_set(), "bootstrap must have been kicked off by the tool call"
    assert result["status"] is False
    assert "still starting" in result["error"]


async def test_a_failed_bootstrap_returns_its_instructions_verbatim(monkeypatch):
    async def fails():
        srv._bootstrap_failure = "GO INSTALL AN ENGINE"
        srv._bootstrap_failed_at = srv.time.monotonic()
        raise bootstrap.BootstrapError("GO INSTALL AN ENGINE")

    monkeypatch.setattr(srv, "_bootstrap", fails)

    result = await srv.memocat_list_keyspaces()
    assert result["status"] is False
    assert result["error"] == "GO INSTALL AN ENGINE"


async def test_a_failed_bootstrap_is_retried_once_the_cooldown_expires(monkeypatch):
    attempts = []

    async def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            srv._bootstrap_failure = "no engine"
            srv._bootstrap_failed_at = srv.time.monotonic()
            raise bootstrap.BootstrapError("no engine")
        srv._bootstrap_failure = None
        return "docker"

    monkeypatch.setattr(srv, "_bootstrap", flaky)
    monkeypatch.setattr(srv, "_RETRY_COOLDOWN", 0.0)

    first = await srv.memocat_list_keyspaces()
    assert first["status"] is False

    # The user started Docker in the meantime; the next call must try again
    # rather than replay the cached failure for the life of the process.
    await srv._engine_ready()
    assert len(attempts) == 2


async def test_an_unexpected_bootstrap_failure_is_recorded_and_retryable(monkeypatch):
    attempts = []

    async def unexpected_then_ready():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("native launcher crashed")
        return "existing"

    monkeypatch.setattr(bootstrap, "ensure_engine", unexpected_then_ready)
    monkeypatch.setattr(srv, "_RETRY_COOLDOWN", 0.0)

    first = await srv.memocat_list_keyspaces()
    assert first == {
        "status": False,
        "payload": None,
        "error": "native launcher crashed",
    }

    await srv._engine_ready()
    assert len(attempts) == 2
    assert srv._bootstrap_failure is None


async def test_explicit_install_waits_for_automatic_acquisition(monkeypatch):
    automatic_started = asyncio.Event()
    release_automatic = asyncio.Event()
    order = []

    async def automatic():
        order.append("automatic-start")
        automatic_started.set()
        await release_automatic.wait()
        order.append("automatic-end")
        return "existing"

    async def install():
        order.append("install")
        return "Engine is already running."

    monkeypatch.setattr(bootstrap, "ensure_engine", automatic)
    monkeypatch.setattr(bootstrap, "install_engine", install)

    automatic_task = srv.start_bootstrap()
    await automatic_started.wait()
    # Keep the installer from launching an unrelated verification task after
    # the ordering under test has completed.
    monkeypatch.setattr(srv, "start_bootstrap", lambda: automatic_task)
    install_task = asyncio.create_task(srv.memocat_install_engine())
    await asyncio.sleep(0)

    assert order == ["automatic-start"]
    assert not install_task.done()

    release_automatic.set()
    result = await install_task
    assert result["status"] is True
    assert order == ["automatic-start", "automatic-end", "install"]


async def test_engine_is_not_bound_before_bootstrap_publishes_credentials(monkeypatch):
    """`_publish` hands credentials over through the environment and
    `_get_engine` caches on first read, so binding early would pin empty
    credentials for the life of the process."""
    order = []

    def fake_get_engine():
        order.append("bind")
        return object()

    async def bootstrap_publishes():
        await asyncio.sleep(0.01)
        order.append("publish")
        return "existing"

    monkeypatch.setattr(srv, "_get_engine", fake_get_engine)
    monkeypatch.setattr(srv, "_bootstrap", bootstrap_publishes)

    await srv._engine_ready()
    srv._get_engine()

    assert order == ["publish", "bind"]


async def test_serving_starts_before_bootstrap_finishes(monkeypatch):
    """The whole point: `initialize` is answered while the engine is still
    being acquired."""
    serving = asyncio.Event()
    bootstrap_done = asyncio.Event()

    async def slow_bootstrap():
        await serving.wait()
        bootstrap_done.set()
        return "existing"

    async def fake_run(_read, _write, _options):
        serving.set()
        await bootstrap_done.wait()

    class FakeStdio:
        async def __aenter__(self):
            return (None, None)

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(srv, "_bootstrap", slow_bootstrap)
    monkeypatch.setattr(srv.mcp._mcp_server, "run", fake_run)
    monkeypatch.setattr(
        "mcp.server.stdio.stdio_server", lambda *_a, **_k: FakeStdio()
    )

    await asyncio.wait_for(srv._run_stdio(), timeout=5)

    assert serving.is_set() and bootstrap_done.is_set()


async def test_stdio_shutdown_awaits_bootstrap_cancellation(monkeypatch):
    bootstrap_started = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def pending_bootstrap():
        bootstrap_started.set()
        try:
            await asyncio.sleep(3600)
        finally:
            await asyncio.sleep(0)
            cleanup_finished.set()

    async def fake_run(_read, _write, _options):
        await bootstrap_started.wait()

    class FakeStdio:
        async def __aenter__(self):
            return (None, None)

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(srv, "_bootstrap", pending_bootstrap)
    monkeypatch.setattr(srv.mcp._mcp_server, "run", fake_run)
    monkeypatch.setattr(
        "mcp.server.stdio.stdio_server", lambda *_a, **_k: FakeStdio()
    )

    await asyncio.wait_for(srv._run_stdio(), timeout=5)

    assert cleanup_finished.is_set()
