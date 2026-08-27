"""Delegated keyspace lifecycle behavior; no live engine required."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def clear_lifecycle_state(server):
    server._resource_sessions.clear()
    server._ks_type_cache.clear()
    server._keyspaces.clear()
    yield
    server._resource_sessions.clear()
    server._ks_type_cache.clear()
    server._keyspaces.clear()


class RemovableKeyspace:
    def __init__(self, result):
        self.result = result
        self.remove_calls = 0

    async def remove_keyspace(self):
        self.remove_calls += 1
        return self.result


@pytest.mark.asyncio
async def test_remove_keyspace_stops_watch_releases_resources_and_clears_caches(
    server, monkeypatch
):
    name = "mem_agent"
    uri = f"memocat://memory/{name}"
    removable = RemovableKeyspace(
        {"status": True, "payload": "removed", "error": None}
    )

    async def persistent(_name):
        return True

    stop = AsyncMock()
    monkeypatch.setattr(server, "_resolve_persistent", persistent)
    monkeypatch.setattr(server, "_keyspace", lambda *_args, **_kwargs: removable)
    monkeypatch.setattr(server.watch_registry, "stop", stop)

    server._resource_sessions[uri] = {1: object()}
    server._ks_type_cache[name] = True
    server._keyspaces[(name, True)] = object()
    server._keyspaces[(name, False)] = object()

    result = await server.memocat_remove_keyspace(scope="agent")

    assert result["status"] is True
    stop.assert_awaited_once_with(name)
    assert uri not in server._resource_sessions
    assert removable.remove_calls == 1
    assert name not in server._ks_type_cache
    assert (name, True) not in server._keyspaces
    assert (name, False) not in server._keyspaces


@pytest.mark.asyncio
async def test_failed_removal_keeps_binding_caches_but_still_releases_watch(
    server, monkeypatch
):
    name = "protected"
    uri = f"memocat://memory/{name}"
    removable = RemovableKeyspace(
        {"status": False, "payload": None, "error": "explicit denial"}
    )

    async def persistent(_name):
        return False

    stop = AsyncMock()
    monkeypatch.setattr(server, "_resolve_persistent", persistent)
    monkeypatch.setattr(server, "_keyspace", lambda *_args, **_kwargs: removable)
    monkeypatch.setattr(server.watch_registry, "stop", stop)

    server._resource_sessions[uri] = {1: object()}
    server._ks_type_cache[name] = False
    persistent_binding = object()
    inmemory_binding = object()
    server._keyspaces[(name, True)] = persistent_binding
    server._keyspaces[(name, False)] = inmemory_binding

    result = await server.memocat_remove_keyspace(keyspace=name)

    assert result["status"] is False
    assert "explicit denial" in result["error"]
    stop.assert_awaited_once_with(name)
    assert uri not in server._resource_sessions
    assert server._ks_type_cache[name] is False
    assert server._keyspaces[(name, True)] is persistent_binding
    assert server._keyspaces[(name, False)] is inmemory_binding


@pytest.mark.asyncio
async def test_removing_missing_keyspace_never_auto_provisions(server, monkeypatch):
    async def missing(_name):
        return None

    keyspace = AsyncMock()
    monkeypatch.setattr(server, "_resolve_persistent", missing)
    monkeypatch.setattr(server, "_keyspace", keyspace)

    result = await server.memocat_remove_keyspace(keyspace="absent")

    assert result["status"] is False
    assert "does not exist or is not visible" in result["error"]
    keyspace.assert_not_called()


@pytest.mark.asyncio
async def test_enable_semantic_is_keyspace_scoped_and_uses_typed_model(
    server, monkeypatch
):
    class SemanticEngine:
        store = "memories"

        def __init__(self):
            self.call = None

        async def enable_semantic_search(self, **kwargs):
            self.call = kwargs
            return {"status": True, "payload": "enabled", "error": None}

    engine = SemanticEngine()
    monkeypatch.setattr(server, "_engine", engine)

    result = await server.memocat_enable_semantic(
        keyspace="research",
        semantic_model="bge-small",
        field="text",
    )

    assert result["status"] is True
    assert engine.call["store"] == "memories"
    assert engine.call["keyspace"] == "research"
    assert engine.call["model"].value == "bge-small"
    assert engine.call["field"] == "text"


@pytest.mark.asyncio
async def test_disable_semantic_is_keyspace_scoped(server, monkeypatch):
    class SemanticEngine:
        store = "memories"

        def __init__(self):
            self.call = None

        async def disable_semantic_search(self, **kwargs):
            self.call = kwargs
            return {"status": True, "payload": "disabled", "error": None}

    engine = SemanticEngine()
    monkeypatch.setattr(server, "_engine", engine)

    result = await server.memocat_disable_semantic(
        keyspace="research", drop_vectors=True
    )

    assert result["status"] is True
    assert engine.call == {
        "drop_vectors": True,
        "store": "memories",
        "keyspace": "research",
    }


@pytest.mark.asyncio
async def test_semantic_status_and_vector_lifecycle_tools(server, monkeypatch):
    class SemanticEngine:
        store = "memories"

        def __init__(self):
            self.calls = []

        async def get_semantic_status(self, **kwargs):
            self.calls.append(("status", kwargs))
            return {"status": True, "payload": "status", "error": None}

        async def enable_precomputed_vector_search(self, *args):
            self.calls.append(("external", args))
            return {"status": True, "payload": "external", "error": None}

        async def reembed_semantic_search(self, *args, **kwargs):
            self.calls.append(("reembed", args, kwargs))
            return {"status": True, "payload": "reembedded", "error": None}

    engine = SemanticEngine()
    monkeypatch.setattr(server, "_engine", engine)

    assert (await server.memocat_semantic_status(keyspace="research"))["status"] is True
    assert engine.calls[-1] == ("status", {"store": "memories", "keyspace": "research"})

    assert (await server.memocat_enable_external_vectors(
        keyspace="research", dimensions=1536, embedding_space="openai:v1"
    ))["status"] is True
    assert engine.calls[-1] == ("external", ("memories", "research", 1536, "openai:v1"))

    assert (await server.memocat_reembed_semantic(
        keyspace="research", semantic_model="bge-small", field="text"
    ))["status"] is True
    kind, args, kwargs = engine.calls[-1]
    assert kind == "reembed"
    assert args[0].value == "bge-small"
    assert args[1:3] == ("memories", "research")
    assert kwargs == {"field": "text"}


@pytest.mark.asyncio
async def test_vector_lifecycle_validation(server, monkeypatch):
    class SemanticEngine:
        store = None

    monkeypatch.setattr(server, "_engine", SemanticEngine())

    with pytest.raises(ValueError, match="dimensions"):
        await server.memocat_enable_external_vectors("k", 0, "space", store="s")
    with pytest.raises(ValueError, match="embedding_space"):
        await server.memocat_enable_external_vectors("k", 3, "", store="s")
    with pytest.raises(ValueError, match="semantic_model"):
        await server.memocat_reembed_semantic("k", "unknown", store="s")
    with pytest.raises(ValueError, match="store is required"):
        await server.memocat_enable_external_vectors("k", 3, "space")


@pytest.mark.asyncio
async def test_semantic_management_validates_scope_before_engine(server, monkeypatch):
    class SemanticEngine:
        store = None

    monkeypatch.setattr(server, "_engine", SemanticEngine())

    with pytest.raises(ValueError, match="keyspace must be"):
        await server.memocat_enable_semantic(keyspace="")
    with pytest.raises(ValueError, match="field must be"):
        await server.memocat_enable_semantic(keyspace="k", field=" ")
    with pytest.raises(ValueError, match="semantic_model"):
        await server.memocat_enable_semantic(
            keyspace="k", semantic_model="unknown"
        )
    with pytest.raises(ValueError, match="store is required"):
        await server.memocat_enable_semantic(keyspace="k")
    with pytest.raises(ValueError, match="store is required"):
        await server.memocat_disable_semantic(keyspace="k")


@pytest.mark.asyncio
async def test_snapshot_tools_are_scoped_to_existing_inmemory_keyspace(
    server, monkeypatch
):
    calls = []

    class SnapshotKeyspace:
        async def do_snaphots_for_keyspace(self):
            calls.append("start")
            return {"status": True, "payload": "started", "error": None}

        async def stop_snapshots_for_keyspace(self):
            calls.append("stop")
            return {"status": True, "payload": "stopped", "error": None}

        async def clean_snapshots_for_keyspace(self):
            calls.append("clean")
            return {"status": True, "payload": "cleaned", "error": None}

    async def inmemory(_name):
        return False

    binding = SnapshotKeyspace()
    monkeypatch.setattr(server, "_resolve_persistent", inmemory)
    monkeypatch.setattr(
        server, "_keyspace",
        lambda name, persistent=None: (
            calls.append(("bind", name, persistent)) or binding
        ),
    )

    assert (await server.memocat_start_snapshots("working"))["status"] is True
    assert (await server.memocat_stop_snapshots("working"))["status"] is True
    assert (await server.memocat_clean_snapshots("working"))["status"] is True
    assert calls == [
        ("bind", "working", False), "start",
        ("bind", "working", False), "stop",
        ("bind", "working", False), "clean",
    ]


@pytest.mark.asyncio
async def test_snapshot_tools_reject_persistent_or_missing_keyspaces(
    server, monkeypatch
):
    async def persistent(_name):
        return True

    monkeypatch.setattr(server, "_resolve_persistent", persistent)
    with pytest.raises(ValueError, match="only for in-memory"):
        await server.memocat_start_snapshots("durable")

    async def missing(_name):
        return None

    monkeypatch.setattr(server, "_resolve_persistent", missing)
    result = await server.memocat_start_snapshots("absent")
    assert result["status"] is False
    assert "does not exist or is not visible" in result["error"]


@pytest.mark.asyncio
async def test_snapshot_rate_configuration_error_is_preserved(server, monkeypatch):
    class SnapshotKeyspace:
        async def do_snaphots_for_keyspace(self):
            return {
                "status": False,
                "payload": None,
                "error": "Snapshot rate is not set",
            }

    async def inmemory(_name):
        return False

    monkeypatch.setattr(server, "_resolve_persistent", inmemory)
    monkeypatch.setattr(
        server, "_keyspace", lambda *_args, **_kwargs: SnapshotKeyspace()
    )

    result = await server.memocat_start_snapshots("working")

    assert result["status"] is False
    assert result["error"] == "Snapshot rate is not set"
