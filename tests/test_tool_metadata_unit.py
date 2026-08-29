"""Directory-facing MCP tool metadata must remain complete and explicit."""

import pytest

from memocat_mcp.server import SERVER_INSTRUCTIONS, mcp


READ_ONLY = {
    "memocat_semantic_search",
    "memocat_recall",
    "memocat_list_memories",
    "memocat_list_keyspaces",
    "memocat_semantic_status",
    "memocat_policy_view",
    "memocat_policy_history",
    "memocat_policy_explain",
    "memocat_await_memory_change",
}

DESTRUCTIVE = {
    "memocat_forget",
    "memocat_remove_keyspace",
    "memocat_clean_snapshots",
    "memocat_reembed_semantic",
    "memocat_disable_semantic",
    # Installs system-wide software behind an administrator prompt.
    # `destructiveHint` is what makes a client confirm first.
    "memocat_install_engine",
}

MUTATING = {
    "memocat_remember",
    "memocat_remember_bulk",
    "memocat_update",
    "memocat_create_keyspace",
    "memocat_enable_semantic",
    "memocat_enable_external_vectors",
    "memocat_start_snapshots",
    "memocat_stop_snapshots",
}


@pytest.mark.asyncio
async def test_all_tools_have_directory_metadata():
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert set(tools) == READ_ONLY | MUTATING | DESTRUCTIVE
    assert len(tools) == 23

    for name, tool in tools.items():
        assert tool.title, f"{name} must have a user-facing title"
        assert tool.annotations is not None, f"{name} must have safety annotations"
        assert tool.annotations.readOnlyHint is not None
        assert tool.annotations.destructiveHint is not None
        assert tool.annotations.idempotentHint is not None
        assert tool.annotations.openWorldHint is not None

    for name in READ_ONLY:
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False

    for name in MUTATING:
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False

    for name in DESTRUCTIVE:
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is True


def test_server_explains_safe_shared_memory_behavior_to_mcp_hosts():
    assert mcp._mcp_server.instructions == SERVER_INSTRUCTIONS
    assert "shared, persistent memory" in SERVER_INSTRUCTIONS
    assert "same Montycat engine" in SERVER_INSTRUCTIONS
    assert "Do not store secrets" in SERVER_INSTRUCTIONS
