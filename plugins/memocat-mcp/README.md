# MemoCat MCP compatibility plugin

MemoCat MCP is now **Montycat MCP**. This compatibility plugin keeps existing
Claude Code and Claude Cowork installations working while launching the current
`montycat-mcp` package.

Montycat MCP 1.0 advertises `montycat_*` tool names. Existing prompts or client
policies that pin `memocat_*` names must be updated.

For new installations, use:

```text
/plugin install montycat-mcp@montygovernance
```

See the [Montycat MCP documentation](../montycat-mcp/README.md) for setup,
privacy, and support information.
