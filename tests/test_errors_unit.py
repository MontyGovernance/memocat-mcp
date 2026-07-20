"""Client-failure normalisation.

The Montycat client signals transport failures by *returning* the string
`"Error: ..."` rather than raising (`send_data` catches everything). Passed
through untouched, an agent receives that string where it expects a result and
has no way to tell a failure from data — the same silent-failure shape that let
a dead subscription report "nothing changed".

No engine required: these drive the boundary helper directly.
"""

from __future__ import annotations

import pytest

CONNECT_ERROR = "Error: [Errno 61] Connect call failed ('127.0.0.1', 21210)"


@pytest.fixture
def call(server):
    return server._call


async def _returns(value):
    return value


async def _raises(exc):
    raise exc


def test_error_string_is_recognised(server):
    assert server._is_client_error(CONNECT_ERROR)
    assert not server._is_client_error({"status": True})
    assert not server._is_client_error("Errors are not always fatal")


@pytest.mark.asyncio
async def test_error_string_becomes_a_failure_envelope(call):
    result = await call(_returns(CONNECT_ERROR))

    assert result["status"] is False
    assert result["payload"] is None
    assert "Connect call failed" in result["error"]
    assert "MONTYCAT_URI" in result["error"], "should say how to fix it"


@pytest.mark.asyncio
async def test_successful_result_passes_through_untouched(call):
    payload = {"status": True, "payload": "key-123", "error": None}
    assert await call(_returns(payload)) is payload


@pytest.mark.asyncio
async def test_ordinary_strings_are_not_mistaken_for_errors(call):
    """Only the client's `Error:` prefix means failure — a stored value that
    happens to be a string must survive."""
    assert await call(_returns("Error handling is hard")) == "Error handling is hard"


@pytest.mark.asyncio
async def test_unexpected_exception_becomes_a_failure_envelope(call):
    result = await call(_raises(ConnectionResetError("engine went away")))

    assert result["status"] is False
    assert "ConnectionResetError" in result["error"]
    assert "engine went away" in result["error"]


@pytest.mark.asyncio
async def test_value_error_still_propagates(call):
    """Bad caller input is the tool's own contract (empty query, missing key) —
    it must keep raising rather than being flattened into a failure payload."""
    with pytest.raises(ValueError):
        await call(_raises(ValueError("No query text provided")))


@pytest.mark.asyncio
async def test_every_tool_reports_failure_when_the_engine_is_unreachable(server, monkeypatch):
    """End-to-end shape check: with no engine, each tool must return a failure
    envelope — never a bare `"Error: ..."` string, which an agent would read as
    a result. Uses a closed port, so it needs no database.
    """
    monkeypatch.setattr(server, "_engine", None)
    monkeypatch.setenv("MONTYCAT_URI", "montycat://x:y@127.0.0.1:59997/deadstore")
    server._keyspaces.clear()
    server._ks_type_cache.clear()

    tools = {
        "semantic_search": server.memocat_semantic_search(query="anything", keyspace="k"),
        "remember": server.memocat_remember(value={"a": 1}, keyspace="k"),
        "remember_bulk": server.memocat_remember_bulk(values=[{"a": 1}], keyspace="k"),
        "recall": server.memocat_recall(keyspace="k", key="1"),
        "list_memories": server.memocat_list_memories(keyspace="k"),
        "list_keyspaces": server.memocat_list_keyspaces(),
        "create_keyspace": server.memocat_create_keyspace(keyspace="k"),
        "update": server.memocat_update(updates={"a": 2}, keyspace="k", key="1"),
        "forget": server.memocat_forget(keyspace="k", key="1"),
        "await_change": server.memocat_await_memory_change(keyspace="k", timeout_sec=2),
    }

    for name, coro in tools.items():
        result = await coro
        assert isinstance(result, dict), f"{name} returned a bare {type(result).__name__}"
        assert result.get("status") is False, f"{name} did not report failure"
        assert result.get("error"), f"{name} reported no reason"

    # caches were populated against the dead engine; leave them clean for others
    server._keyspaces.clear()
    server._ks_type_cache.clear()
