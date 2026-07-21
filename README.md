#Memocat — MCP Server for Montycat

**Give your AI agents self-hosted, semantically-searchable long-term memory.**
`memocat-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io)
server for [Montycat](https://montygovernance.com) — a Rust-powered vector
database and NoSQL store in one engine. Agents *remember* facts and *recall*
them by meaning (vector search) or by key, on your own hardware, with on-device
embeddings and no external API.

No cloud vector database. No embedding service. No per-query bill. Your memory,
your machine.

## Why

LLM agents forget everything between runs. Bolt on Montycat and they don't:
every fact your agent stores is embedded and indexed automatically, then
recalled by meaning through a handful of MCP tools. It is the retrieval layer
for RAG and the memory layer for agents — one self-hosted engine, not a stack of
rented services.

## Tools

| Tool | What it does |
|------|--------------|
| `memocat_semantic_search` | Recall by **meaning** (vector kNN) — the core RAG/memory tool. |
| `memocat_remember` | Store a fact/record; embedded + indexed automatically. |
| `memocat_remember_bulk` | Store many memories at once. |
| `memocat_recall` | Fetch by exact key or by field filter. |
| `memocat_list_memories` | Browse / list stored memories (optionally most-recent first). |
| `memocat_update` | Revise a memory in place — memory is mutable. |
| `memocat_forget` | Delete a stored record. |
| `memocat_list_keyspaces` | Discover available memory namespaces. |
| `memocat_create_keyspace` | Provision a new memory namespace. |
| `memocat_await_memory_change` | **Wait for memory to change** — returns the moment another agent or session writes. Live subscription, not polling. |

## Real-time memory watch

Other memory servers can only be polled: ask again, and again, in case something
changed. Montycat has **native live subscriptions**, so this one pushes.

```
agent B: memocat_await_memory_change(scope="shared", timeout_sec=60)
                    ⏳ sleeps — no polling, no wasted tokens
agent A: memocat_remember({"text": "the deploy key rotated"}, scope="shared")
agent B: ← returns in milliseconds with the key, the value, and the event
```

Two agents, one shared scope, one notices what the other just learned. Pass the
returned `next_seq` back as `since_seq` to resume exactly where you left off —
changes that happen between calls are buffered, not lost.

Memory namespaces are also exposed as MCP **resources**
(`memocat://memory/<keyspace>`) with `resources.subscribe` support, so clients
that implement resource subscriptions get `notifications/resources/updated`
pushed to them as well. Both surfaces share one engine subscription.

Subscriptions open on demand and close when idle
(`MONTYCAT_WATCH_IDLE_TIMEOUT`), so users who never watch pay nothing.

## Requirements

- A running **Montycat Semantic** engine (semantic search is on by default there).

  **Pick the tag for your CPU** — the tag carries the architecture:

  **Apple Silicon (M1/M2/M3/M4) — use `arm64-semantic`:**
  ```bash
  docker run -d --name montycat -p 21210:21210 -p 21211:21211 \
    -e MONTYCAT_SUPEROWNER="admin" -e MONTYCAT_PASSWORD="change-me" \
    -v montycat_data:/var/lib/.montycat \
    montygovernance/montycat:arm64-semantic
  ```

  **Intel / AMD (x86_64) — use `semantic`:**
  ```bash
  docker run -d --name montycat -p 21210:21210 -p 21211:21211 \
    -e MONTYCAT_SUPEROWNER="admin" -e MONTYCAT_PASSWORD="change-me" \
    -v montycat_data:/var/lib/.montycat \
    montygovernance/montycat:semantic
  ```

  > On Apple Silicon the plain `semantic` tag is the amd64 image and runs under
  > emulation, where the embedding runtime's warm-up crashes. Use
  > `arm64-semantic` — a native build, not a workaround. Unsure which you have?
  > `uname -m` prints `arm64` on Apple Silicon and `x86_64` on Intel.

  Port `21211` is the subscription server and is required for
  `memocat_await_memory_change` (real-time watch); without it the other tools
  still work.

- Python 3.10+ (via `uv` / `uvx`).

## Install & run

```bash
uvx memocat-mcp
```

Configure the connection with environment variables (see below).

## Docker Compose deployment

Use Compose when you want a reproducible local deployment with a persistent
Semantic engine and an MCP container on the same private Docker network. Docker
is optional when you already manage a reachable Montycat server.

Create a `.env` file beside `compose.yaml`:

```dotenv
MONTYCAT_USERNAME=admin
MONTYCAT_PASSWORD=replace-with-a-strong-password
MONTYCAT_STORE=memories
# Apple Silicon: arm64-semantic. Intel/AMD64: semantic.
MONTYCAT_IMAGE_TAG=semantic
# Local checkout containing the unpublished montycat 1.0.7 client.
MONTYCAT_CLIENT_PATH=/absolute/path/to/montycat_python
```

Start the engine and build the MCP image:

```bash
docker compose up -d montycat
docker compose build mcp
```

`montycat` 1.0.7 is currently local-only and provides the required hybrid
semantic-search methods. The image installs it from `MONTYCAT_CLIENT_PATH`
through Compose's named build context; this is intentional and never falls back
to an older PyPI client. Once 1.0.7 is published, replace that local context
with a pinned published dependency before distributing the image externally.

The engine data is stored in the named `montycat_data` volume. Ports `21210`
and `21211` are published for debugging and external clients; the MCP container
uses the private `montycat:21210` network address. Credentials are passed as
separate environment variables, so passwords with URL-special characters need
no URL encoding. Port `21211` carries live subscription traffic for
`memocat_await_memory_change`.

MCP uses stdio, so do **not** run it as a web service. Configure a desktop MCP
client to invoke the Compose service on demand:

```json
{
  "mcpServers": {
    "memocat": {
      "command": "docker",
      "args": [
        "compose",
        "-f", "/absolute/path/to/montycat_mcp/compose.yaml",
        "run", "--rm", "-T", "mcp"
      ]
    }
  }
}
```

For Apple Silicon set `MONTYCAT_IMAGE_TAG=arm64-semantic` in `.env`; the plain
`semantic` image is AMD64. Stop the stack with `docker compose down`; include
`-v` only when you intentionally want to erase persisted memories.

### Engine auto-start

MemoCat first reuses an engine already reachable through `MONTYCAT_URI` or the
host/port settings. If none is running, it looks for an installed
`montycat_bin` and then attempts the official platform route:

| Platform | Route | If it cannot complete |
|---|---|---|
| macOS | Download verified `.pkg` installer and open it (may prompt for admin approval) | Docker |
| Windows x86_64 | Download verified `.msi` and invoke Windows Installer (may prompt for UAC) | Docker |
| Linux AMD64 | Run the official one-command APT setup for `montycat-semantic` (may prompt for sudo) | Docker |
| Other platforms | — | Docker |

The package URLs currently use a temporary predictable naming convention while
the downloads site publishes its installer manifest. Override it with
`MEMOCAT_INSTALLER_URL` if needed. On Linux, set
`MEMOCAT_APT_INSTALL_COMMAND` to use an organization-managed mirror or package
command. ARM64 Linux goes directly to Docker because the official APT repository
is AMD64-only. Set `MEMOCAT_AUTOSTART=off` to disable all installation/start
attempts.

## Use with Claude Desktop / Cursor

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "memocat": {
      "command": "uvx",
      "args": ["memocat-mcp"],
      "env": {
        "MONTYCAT_URI": "montycat://admin:change-me@localhost:21210/mystore"
      }
    }
  }
}
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MONTYCAT_URI` | — | `montycat://user:pass@host:port/store` (preferred; overrides the parts below) |
| `MONTYCAT_HOST` | `127.0.0.1` | Engine host |
| `MONTYCAT_PORT` | `21210` | Engine port |
| `MONTYCAT_USERNAME` / `MONTYCAT_PASSWORD` | — | Credentials |
| `MONTYCAT_STORE` | — | Store name |
| `MONTYCAT_TLS` | `false` | Connect over TLS |
| `MONTYCAT_DEFAULT_KEYSPACE` | `memory` | Keyspace used when a tool omits scope/keyspace |
| `MONTYCAT_PERSISTENT` | `true` | Storage type for **newly created** keyspaces (durable vs in-memory). Existing keyspaces are auto-detected — the server binds the correct type regardless of this setting. |
| `MONTYCAT_SCOPE` | — | Default owner/scope, applied when a tool omits `scope` |
| `MONTYCAT_SCOPE_PREFIX` | `mem_` | Prefix for per-owner keyspaces (`mem_<scope>`) |
| `MONTYCAT_SHARED_KEYSPACE` | `mem_shared` | The common/shared keyspace name |
| `MONTYCAT_AUTO_PROVISION` | `true` | Auto-create a scope's keyspace on first use (needs superowner) |
| `MONTYCAT_AUTO_TIMESTAMP` | `true` | Stamp each memory with an indexed `_created_at`, enabling time-range recall (`since`/`until`). Costs a server-side timestamp parse per write — turn off if memories are never recalled by time. |
| `MONTYCAT_SUBSCRIPTION_PORT` | main + 1 | Engine subscription server port (21211 by default; enabled by default) |
| `MONTYCAT_WATCH_BUFFER` | `500` | Changes retained per watched keyspace, so changes between calls aren't lost |
| `MONTYCAT_WATCH_IDLE_TIMEOUT` | `300` | Seconds before an unused subscription is closed |

`memocat_create_keyspace` and `memocat_forget` require superowner credentials.

## Memory scoping (multi-tenant)

Pass `scope` (an owner/user id) to any memory tool to isolate that owner's
memory. Because Montycat's semantic search runs per keyspace, each scope gets its
own keyspace `mem_<scope>` — so semantic recall for one owner never sees another
owner's memories:

```
remember(value={"fact": "..."}, scope="alice")      # -> keyspace mem_alice
semantic_search(query="...", scope="alice")          # searches only mem_alice
remember(value={"fact": "..."}, scope="shared")      # -> the shared keyspace
```

- **Per-owner private memory** — `scope="<owner>"` → `mem_<owner>`, auto-created
  on first use (with superowner creds).
- **Shared/common memory** — `scope="shared"` → the `MONTYCAT_SHARED_KEYSPACE`.
- **Group memory** — use a group id as the scope (e.g. `scope="team_eng"`).
- **Single-tenant** — set `MONTYCAT_SCOPE` once and omit `scope` per call.

This maps onto Montycat's per-keyspace RBAC: `grant_to(owner, permission,
keyspaces=["mem_alice"])` gives an owner access to only their keyspace.

**Isolation note:** with one server instance sharing one connection, scoping is
*logical* isolation (by keyspace). For **credential-enforced** isolation, run one
server instance per owner, each connecting with that owner's own credentials —
then the engine itself denies cross-owner access.

## Links

- Montycat: https://montygovernance.com
- Docs: https://montygovernance.com/docs
- Engine on Docker Hub: https://hub.docker.com/r/montygovernance/montycat
- Python client (PyPI): https://pypi.org/project/montycat/

## License

MIT.
