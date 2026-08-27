"""Live coverage for MCP surfaces added with the 1.3 semantic API."""

from __future__ import annotations

import pytest

from .conftest import requires_engine

pytestmark = [pytest.mark.asyncio, requires_engine]


def payload(result):
    assert result.get("status") is True, result
    return result.get("payload")


async def test_external_vectors_write_search_update_and_status(server, keyspace):
    """MCP forwards vector arguments and external-profile lifecycle calls."""
    # This engine auto-enrolls newly created keyspaces for text embeddings.
    # Switching to a different embedding space is intentionally explicit.
    payload(await server.memocat_disable_semantic(
        keyspace=keyspace, drop_vectors=True
    ))
    enrolled = await server.memocat_enable_external_vectors(
        keyspace=keyspace,
        dimensions=3,
        embedding_space="memocat-mcp-live-test:v1",
    )
    payload(enrolled)

    stored = payload(await server.memocat_remember(
        keyspace=keyspace,
        value={"text": "vector memory"},
        vector=[1.0, 0.0, 0.0],
    ))

    payload(await server.memocat_update(
        keyspace=keyspace,
        key=str(stored),
        updates={"state": "updated"},
        vector=[0.0, 1.0, 0.0],
    ))
    payload(await server.memocat_remember_bulk(
        keyspace=keyspace,
        values=[{"text": "bulk vector"}],
        vectors=[[0.0, 0.0, 1.0]],
    ))

    hits = payload(await server.memocat_semantic_search(
        keyspace=keyspace,
        query="",
        vector=[0.0, 1.0, 0.0],
        limit=5,
    ))
    assert hits
    assert any(hit["__value__"].get("state") == "updated" for hit in hits)

    status = payload(await server.memocat_semantic_status(keyspace=keyspace))
    assert keyspace in str(status)


async def test_reembed_semantic_is_available_through_mcp(server, keyspace):
    """Re-embedding is a keyspace-scoped MCP operation, not a raw command."""
    payload(await server.memocat_enable_semantic(
        keyspace=keyspace,
        semantic_model="bge-small",
    ))
    result = await server.memocat_reembed_semantic(
        keyspace=keyspace,
        semantic_model="bge-small",
    )
    payload(result)
