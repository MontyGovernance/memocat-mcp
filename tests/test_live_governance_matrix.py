"""The delegated-owner governance acceptance matrix.

Each test names the numbered scenarios from DATA_MESH_GOVERNANCE_PLAN.md.
All principals and keyspaces are unique and removed in ``GovernanceHarness``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from montycat import (
    Engine,
    Keyspace,
    PolicyCapability,
    PolicyKeyspaceType,
    SemanticModel,
)

from .conftest import requires_engine

pytestmark = [pytest.mark.asyncio, requires_engine]


def payload(result):
    return result.get("payload") if isinstance(result, dict) else None


class GovernanceHarness:
    def __init__(self, server):
        self.server = server
        self.admin = server._get_engine()
        self.owners: list[str] = []
        self.keyspaces: dict[str, bool] = {}

    def unique(self, kind: str) -> str:
        return f"memocat_t_{kind}_{uuid.uuid4().hex[:10]}"

    async def owner(self, kind: str = "owner") -> tuple[str, Engine]:
        username = self.unique(kind)
        password = uuid.uuid4().hex
        result = await self.admin.create_owner(username, password)
        assert result.get("status") is True, result
        self.owners.append(username)
        return username, Engine(
            host=self.admin.host,
            port=self.admin.port,
            username=username,
            password=password,
            store=self.admin.store,
            tls=self.admin.tls,
        )

    async def grant_provision(
        self,
        username: str,
        *,
        types: list[PolicyKeyspaceType],
        models: list[SemanticModel] | None = None,
    ):
        result = await self.admin.policy_grant(
            owner=username,
            capability=PolicyCapability.PROVISION_KEYSPACE,
            store=self.admin.store,
            types=types,
            models=models,
        )
        assert result.get("status") is True, result

    def use(self, engine: Engine) -> None:
        self.server._engine = engine
        self.server._keyspaces.clear()
        self.server._ks_type_cache.clear()

    def admin_keyspace(self, name: str, persistent: bool):
        base = Keyspace.Persistent if persistent else Keyspace.InMemory
        cls = type(f"Admin_{name}", (base,), {"keyspace": name})
        cls.connect_engine(self.admin)
        return cls

    async def create_as_admin(self, name: str, persistent: bool = True):
        self.use(self.admin)
        result = await self.server.memocat_create_keyspace(
            keyspace=name,
            storage="persistent" if persistent else "inmemory",
        )
        assert result.get("status") is True, result
        self.keyspaces[name] = persistent
        return self.admin_keyspace(name, persistent)

    async def cleanup(self):
        from memocat_mcp.watch import registry

        await registry.stop_all()
        self.use(self.admin)
        # Retirement transfers creator-owned resources to the superowner,
        # making deterministic cleanup possible even after denial tests.
        for owner in reversed(self.owners):
            await self.admin.remove_owner(owner)
        for name, persistent in reversed(list(self.keyspaces.items())):
            await self.admin_keyspace(name, persistent).remove_keyspace()


@pytest.fixture
async def governance(server):
    harness = GovernanceHarness(server)
    try:
        yield harness
    finally:
        await harness.cleanup()


async def test_matrix_01_02_06_07_17_creator_lifecycle_and_policy_view(governance):
    """1,2 provision both tiers; 6 creator read/write; 7 creator removal;
    17 effective view reports automatic creator capabilities."""
    g = governance
    username, owner = await g.owner()
    await g.grant_provision(
        username,
        types=[PolicyKeyspaceType.PERSISTENT, PolicyKeyspaceType.IN_MEMORY],
        models=[SemanticModel.BGE_SMALL],
    )
    g.use(owner)

    persistent = g.unique("persistent")
    remembered = await g.server.memocat_remember(
        keyspace=persistent, value={"text": "creator can write"}
    )
    assert remembered.get("status") is True, remembered
    g.keyspaces[persistent] = True
    recalled = await g.server.memocat_recall(
        keyspace=persistent, key=str(payload(remembered))
    )
    assert recalled.get("status") is True, recalled

    inmemory = g.unique("inmemory")
    created = await g.server.memocat_create_keyspace(
        keyspace=inmemory, storage="inmemory"
    )
    assert created.get("status") is True, created
    g.keyspaces[inmemory] = False

    view = await g.server.memocat_policy_view()
    owned = {item["keyspace"]: item for item in payload(view)["owned_keyspaces"]}
    assert persistent in owned and inmemory in owned
    assert {"read", "write", "remove-keyspace", "manage-semantic"}.issubset(
        owned[persistent]["effective_creator_capabilities"]
    )
    assert "manage-snapshots" in owned[inmemory]["effective_creator_capabilities"]

    removed = await g.server.memocat_remove_keyspace(keyspace=inmemory)
    assert removed.get("status") is True, removed
    g.keyspaces.pop(inmemory)


async def test_matrix_03_disallowed_keyspace_type_is_rejected(governance):
    g = governance
    username, owner = await g.owner()
    await g.grant_provision(username, types=[PolicyKeyspaceType.IN_MEMORY])
    g.use(owner)

    result = await g.server.memocat_create_keyspace(
        keyspace=g.unique("denied_persistent"), storage="persistent"
    )
    assert result.get("status") is False, result


async def test_matrix_04_05_semantic_model_constraints(governance):
    g = governance
    username, owner = await g.owner()
    await g.grant_provision(
        username,
        types=[PolicyKeyspaceType.PERSISTENT],
        models=[SemanticModel.BGE_SMALL],
    )
    g.use(owner)
    name = g.unique("models")
    created = await g.server.memocat_create_keyspace(
        keyspace=name, storage="persistent"
    )
    assert created.get("status") is True, created
    g.keyspaces[name] = True

    denied = await g.server.memocat_enable_semantic(
        keyspace=name, semantic_model="bge-base"
    )
    assert denied.get("status") is False, denied

    allowed = await g.server.memocat_enable_semantic(
        keyspace=name, semantic_model="bge-small"
    )
    assert allowed.get("status") is True, allowed


async def test_matrix_08_09_explicit_creator_denials(governance):
    g = governance
    username, owner = await g.owner()
    await g.grant_provision(
        username,
        types=[PolicyKeyspaceType.PERSISTENT],
        models=[SemanticModel.BGE_SMALL],
    )
    g.use(owner)
    name = g.unique("denials")
    created = await g.server.memocat_create_keyspace(
        keyspace=name, storage="persistent"
    )
    assert created.get("status") is True, created
    g.keyspaces[name] = True

    for capability in (
        PolicyCapability.REMOVE_KEYSPACE,
        PolicyCapability.MANAGE_SEMANTIC,
    ):
        denied = await g.admin.policy_deny(
            owner=username,
            capability=capability,
            store=g.admin.store,
            keyspace=name,
        )
        assert denied.get("status") is True, denied

    semantic = await g.server.memocat_enable_semantic(
        keyspace=name, semantic_model="bge-small"
    )
    assert semantic.get("status") is False, semantic
    removed = await g.server.memocat_remove_keyspace(keyspace=name)
    assert removed.get("status") is False, removed


async def test_matrix_10_11_12_13_14_cross_owner_isolation(
    governance, monkeypatch
):
    """10/11 data denial; 12 filtered listing; 13 resource denial;
    14 subscription handshake denial."""
    g = governance
    owner_a_name, owner_a = await g.owner("owner_a")
    _, owner_b = await g.owner("owner_b")
    await g.grant_provision(
        owner_a_name, types=[PolicyKeyspaceType.PERSISTENT]
    )
    g.use(owner_a)
    name = g.unique("private")
    written = await g.server.memocat_remember(
        keyspace=name, value={"text": "owner A only"}
    )
    assert written.get("status") is True, written
    g.keyspaces[name] = True

    g.use(owner_b)
    read = await g.server.memocat_recall(
        keyspace=name, key=str(payload(written))
    )
    assert read.get("status") is False, read
    write = await g.server.memocat_remember(
        keyspace=name, value={"text": "intrusion"}
    )
    assert write.get("status") is False, write

    listed = await g.server.memocat_list_keyspaces()
    assert name not in str(payload(listed))

    resource = await g.server.memory_resource(name)
    assert resource.get("status") is False, resource

    monkeypatch.setattr(g.server, "_request_session", lambda: object())
    with pytest.raises((g.server.KeyspaceBindingError, PermissionError)):
        await g.server._subscribe_resource(f"memocat://memory/{name}")

    owner_b_keyspace = type(
        f"OwnerB_{name}", (Keyspace.Persistent,), {"keyspace": name}
    )
    owner_b_keyspace.connect_engine(owner_b)
    from memocat_mcp.watch import MemoryWatch

    watch = MemoryWatch(name)
    await watch.start(owner_b_keyspace)
    problem = await watch.ensure_established(grace=3)
    await watch.stop()
    assert problem is not None
    assert watch.running is False


async def test_matrix_18_policy_history_is_owner_scoped(governance):
    g = governance
    owner_a_name, owner_a = await g.owner("history_a")
    owner_b_name, _ = await g.owner("history_b")
    await g.grant_provision(owner_a_name, types=[PolicyKeyspaceType.PERSISTENT])
    await g.grant_provision(owner_b_name, types=[PolicyKeyspaceType.IN_MEMORY])

    g.use(owner_a)
    history = await g.server.memocat_policy_history()
    text = str(payload(history))
    assert owner_a_name in text
    assert owner_b_name not in text


async def test_matrix_19_owner_retirement_transfers_keyspaces(governance):
    g = governance
    username, owner = await g.owner("retire")
    await g.grant_provision(username, types=[PolicyKeyspaceType.PERSISTENT])
    g.use(owner)
    name = g.unique("retired")
    created = await g.server.memocat_create_keyspace(
        keyspace=name, storage="persistent"
    )
    assert created.get("status") is True, created
    g.keyspaces[name] = True

    retired = await g.admin.remove_owner(username)
    assert retired.get("status") is True, retired
    g.owners.remove(username)
    admin_view = await g.admin.policy_view()
    resource = next(
        item for item in payload(admin_view)["resources"]
        if item["keyspace"] == name
    )
    assert resource["owner_principal_id"] == "superowner"


async def test_matrix_20_legacy_permissions_remain_compatible(governance):
    g = governance
    username, owner = await g.owner("legacy")
    name = g.unique("legacy")
    admin_keyspace = await g.create_as_admin(name)
    granted = await g.admin.grant_to(username, "all", keyspaces=[name])
    assert granted.get("status") is True, granted

    g.use(owner)
    written = await g.server.memocat_remember(
        keyspace=name, value={"text": "legacy permission"}
    )
    assert written.get("status") is True, written
    recalled = await g.server.memocat_recall(
        keyspace=name, key=str(payload(written))
    )
    assert recalled.get("status") is True, recalled
    assert admin_keyspace is not None


async def test_matrix_21_superowner_behavior_is_preserved(governance):
    g = governance
    g.use(g.admin)
    name = g.unique("superowner")
    created = await g.server.memocat_create_keyspace(
        keyspace=name, storage="persistent"
    )
    assert created.get("status") is True, created
    g.keyspaces[name] = True
    written = await g.server.memocat_remember(
        keyspace=name, value={"text": "admin"}
    )
    assert written.get("status") is True, written
    removed = await g.server.memocat_remove_keyspace(keyspace=name)
    assert removed.get("status") is True, removed
    g.keyspaces.pop(name)


async def test_matrix_22_auto_provision_denial_has_policy_explanation(governance):
    g = governance
    _, owner = await g.owner("no_provision")
    g.use(owner)
    result = await g.server.memocat_remember(
        keyspace=g.unique("denied_auto"), value={"text": "cannot create"}
    )
    assert result.get("status") is False, result
    assert "Policy explanation" in result.get("error", "")
