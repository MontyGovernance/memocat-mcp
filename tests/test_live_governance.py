"""Live delegated-lifecycle checks against a Montycat Semantic engine."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from montycat import Engine, Keyspace

from .conftest import requires_engine

pytestmark = [pytest.mark.asyncio, requires_engine]


async def test_keyspace_scoped_semantic_lifecycle(server):
    name = f"memocat_t_semantic_{uuid.uuid4().hex[:10]}"

    created = await server.memocat_create_keyspace(
        keyspace=name, storage="persistent"
    )
    assert created.get("status") is True

    try:
        enabled = await server.memocat_enable_semantic(
            keyspace=name, semantic_model="bge-small"
        )
        assert enabled.get("status") is True, enabled

        disabled = await server.memocat_disable_semantic(
            keyspace=name, drop_vectors=False
        )
        assert disabled.get("status") is True, disabled
    finally:
        removed = await server.memocat_remove_keyspace(keyspace=name)
        assert removed.get("status") is True, removed


async def test_keyspace_scoped_snapshot_lifecycle_or_configuration_error(server):
    name = f"memocat_t_snapshot_{uuid.uuid4().hex[:10]}"

    created = await server.memocat_create_keyspace(
        keyspace=name, storage="inmemory"
    )
    assert created.get("status") is True

    try:
        started = await server.memocat_start_snapshots(keyspace=name)
        if started.get("status") is False:
            assert started.get("error") == "Snapshot rate is not set", started
        else:
            stopped = await server.memocat_stop_snapshots(keyspace=name)
            assert stopped.get("status") is True, stopped

            cleaned = await server.memocat_clean_snapshots(keyspace=name)
            assert cleaned.get("status") is True, cleaned
    finally:
        removed = await server.memocat_remove_keyspace(keyspace=name)
        assert removed.get("status") is True, removed


async def test_read_revocation_closes_watch_and_purges_buffer(server, monkeypatch):
    """The engine authorizes subscriptions only when they open. MemoCat's
    lease must catch a later revoke and make already-buffered data unreplayable.
    """
    from memocat_mcp.watch import registry

    admin = server._get_engine()
    name = f"memocat_t_revoke_{uuid.uuid4().hex[:10]}"
    owner = f"memocat_owner_{uuid.uuid4().hex[:10]}"
    password = uuid.uuid4().hex
    admin_keyspace = server._keyspace(name, persistent=True)

    monkeypatch.setenv("MONTYCAT_WATCH_AUTH_LEASE_SEC", "1")

    created_owner = await admin.create_owner(owner, password)
    assert created_owner.get("status") is True, created_owner
    created_keyspace = await server.memocat_create_keyspace(
        keyspace=name, storage="persistent"
    )
    assert created_keyspace.get("status") is True, created_keyspace
    granted = await admin.grant_to(owner, "read", keyspaces=[name])
    assert granted.get("status") is True, granted

    owner_engine = Engine(
        host=admin.host,
        port=admin.port,
        username=owner,
        password=password,
        store=admin.store,
        tls=admin.tls,
    )

    try:
        server._engine = owner_engine
        server._keyspaces.clear()
        server._ks_type_cache.clear()

        opened = await server.memocat_await_memory_change(
            keyspace=name, timeout_sec=1
        )
        assert opened.get("status") is True, opened
        watch = registry.get(name)
        assert watch is not None and watch.running

        inserted = await admin_keyspace.insert_value({"text": "must be purged"})
        assert inserted.get("status") is True, inserted

        deadline = asyncio.get_running_loop().time() + 3
        while not watch.changes and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert watch.changes, "test must prove data was buffered before revocation"

        revoked = await admin.revoke_from(owner, "read", keyspaces=[name])
        assert revoked.get("status") is True, revoked

        deadline = asyncio.get_running_loop().time() + 5
        while watch.revoked_error is None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert watch.revoked_error is not None
        assert list(watch.changes) == []
        assert watch.running is False

        replay = await server.memocat_await_memory_change(
            keyspace=name, timeout_sec=1, since_seq=0
        )
        assert replay.get("status") is False
        assert "Buffered changes were purged" in replay.get("error", "")
    finally:
        await registry.stop_all()
        server._engine = admin
        server._keyspaces.clear()
        server._ks_type_cache.clear()
        await admin.remove_owner(owner)
        await admin_keyspace.remove_keyspace()
