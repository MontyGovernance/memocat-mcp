"""Read-only governance tool contracts; no live engine required."""

from __future__ import annotations

import pytest


class FakePolicyEngine:
    store = "memories"

    def __init__(self):
        self.view_call = None
        self.history_call = None
        self.explain_call = None

    async def policy_view(self, **kwargs):
        self.view_call = kwargs
        return {"status": True, "payload": {"owner": "memory-agent"}, "error": None}

    async def policy_explain(self, **kwargs):
        self.explain_call = kwargs
        return {"status": True, "payload": {"allowed": True}, "error": None}

    async def policy_history(self, **kwargs):
        self.history_call = kwargs
        return {"status": True, "payload": {"events": []}, "error": None}


@pytest.mark.asyncio
async def test_policy_view_uses_authenticated_owner_and_configured_store(
    server, monkeypatch
):
    engine = FakePolicyEngine()
    monkeypatch.setattr(server, "_engine", engine)

    result = await server.montycat_policy_view()

    assert result["status"] is True
    assert engine.view_call == {"store": "memories"}
    assert "owner" not in engine.view_call


@pytest.mark.asyncio
async def test_policy_view_accepts_explicit_store(server, monkeypatch):
    engine = FakePolicyEngine()
    monkeypatch.setattr(server, "_engine", engine)

    await server.montycat_policy_view(store="research")

    assert engine.view_call == {"store": "research"}


@pytest.mark.asyncio
async def test_policy_history_is_owner_scoped_and_filterable(server, monkeypatch):
    engine = FakePolicyEngine()
    monkeypatch.setattr(server, "_engine", engine)

    result = await server.montycat_policy_history(keyspace="agent-memory")

    assert result["status"] is True
    assert engine.history_call == {
        "store": "memories",
        "keyspace": "agent-memory",
    }
    assert "owner" not in engine.history_call


@pytest.mark.asyncio
async def test_policy_history_requires_store_for_keyspace_filter(server, monkeypatch):
    engine = FakePolicyEngine()
    engine.store = None
    monkeypatch.setattr(server, "_engine", engine)

    with pytest.raises(ValueError, match="store is required"):
        await server.montycat_policy_history(keyspace="agent-memory")


@pytest.mark.asyncio
async def test_policy_explain_converts_public_values_to_sdk_enums(
    server, monkeypatch
):
    engine = FakePolicyEngine()
    monkeypatch.setattr(server, "_engine", engine)

    result = await server.montycat_policy_explain(
        capability="provision-keyspace",
        keyspace="agent-memory",
        storage="persistent",
        semantic_model="bge-small",
    )

    assert result["status"] is True
    call = engine.explain_call
    assert call["capability"].value == "provision-keyspace"
    assert call["store"] == "memories"
    assert call["keyspace"] == "agent-memory"
    assert call["keyspace_type"].value == "persistent"
    assert call["model"].value == "bge-small"
    assert "owner" not in call


@pytest.mark.asyncio
async def test_policy_explain_rejects_invalid_values_before_engine(
    server, monkeypatch
):
    engine = FakePolicyEngine()
    monkeypatch.setattr(server, "_engine", engine)

    with pytest.raises(ValueError, match="capability must be one of"):
        await server.montycat_policy_explain(capability="read")
    with pytest.raises(ValueError, match="storage must be one of"):
        await server.montycat_policy_explain(
            capability="provision-keyspace", storage="disk"
        )
    with pytest.raises(ValueError, match="semantic_model must be one of"):
        await server.montycat_policy_explain(
            capability="manage-semantic", semantic_model="giant"
        )

    assert engine.explain_call is None


@pytest.mark.asyncio
async def test_policy_explain_requires_a_store(server, monkeypatch):
    engine = FakePolicyEngine()
    engine.store = None
    monkeypatch.setattr(server, "_engine", engine)

    with pytest.raises(ValueError, match="store is required"):
        await server.montycat_policy_explain(capability="manage-snapshots")


@pytest.mark.asyncio
async def test_auto_provision_failure_includes_policy_explanation(server, monkeypatch):
    class DeniedKeyspace:
        async def create_keyspace(self):
            return {
                "status": False,
                "payload": None,
                "error": "permission denied",
            }

    class DeniedEngine:
        store = "memories"

        async def get_structure_available(self):
            return {"status": True, "payload": {"structure": {}}, "error": None}

        async def policy_explain(self, **kwargs):
            assert kwargs["capability"].value == "provision-keyspace"
            assert kwargs["keyspace"] == "mem_agent"
            assert kwargs["keyspace_type"].value == "persistent"
            return {
                "status": True,
                "payload": {
                    "allowed": False,
                    "reason": "persistent provisioning is denied",
                },
                "error": None,
            }

    monkeypatch.setattr(server, "_engine", DeniedEngine())
    monkeypatch.setattr(
        server, "_keyspace", lambda *_args, **_kwargs: DeniedKeyspace()
    )
    server._ks_type_cache.clear()

    result = await server.montycat_remember(
        value={"text": "remember me"}, scope="agent"
    )

    assert result["status"] is False
    assert "permission denied" in result["error"]
    assert "Policy explanation" in result["error"]
    assert "persistent provisioning is denied" in result["error"]


@pytest.mark.asyncio
async def test_auto_provision_keeps_original_error_when_explanation_fails(
    server, monkeypatch
):
    class DeniedKeyspace:
        async def create_keyspace(self):
            return {"status": False, "payload": None, "error": "quota exceeded"}

    class OlderEngine:
        store = "memories"

        async def get_structure_available(self):
            return {"status": True, "payload": {"structure": {}}, "error": None}

    monkeypatch.setattr(server, "_engine", OlderEngine())
    monkeypatch.setattr(
        server, "_keyspace", lambda *_args, **_kwargs: DeniedKeyspace()
    )
    server._ks_type_cache.clear()

    result = await server.montycat_remember(
        value={"text": "remember me"}, scope="agent"
    )

    assert result["status"] is False
    assert "quota exceeded" in result["error"]
    assert "Policy explanation" not in result["error"]


@pytest.mark.asyncio
async def test_watch_authorization_uses_uncached_filtered_structure(
    server, monkeypatch
):
    class StructureEngine:
        def __init__(self):
            self.visible = True
            self.calls = 0

        async def get_structure_available(self):
            self.calls += 1
            persistent = {"private": {}} if self.visible else {}
            return {
                "status": True,
                "payload": {
                    "structure": {
                        "memories": {
                            "persistent": persistent,
                            "inmemory": {},
                        }
                    }
                },
                "error": None,
            }

    engine = StructureEngine()
    monkeypatch.setattr(server, "_engine", engine)
    server._ks_type_cache["private"] = True

    assert await server._authorize_watch("private") is None
    engine.visible = False
    problem = await server._authorize_watch("private")

    assert "no longer includes" in problem
    assert engine.calls == 2
