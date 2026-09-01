# MemoCat — Shared Memory for AI Agents and Systems

<!-- mcp-name: io.github.MontyGovernance/memocat-mcp -->

[![PyPI](https://img.shields.io/pypi/v/memocat-mcp.svg)](https://pypi.org/project/memocat-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/memocat-mcp.svg)](https://pypi.org/project/memocat-mcp/)
[![License](https://img.shields.io/github/license/MontyGovernance/memocat-mcp.svg)](https://github.com/MontyGovernance/memocat-mcp/blob/master/LICENSE)

**MemoCat gives Claude, OpenAI/Codex, Cursor, and other MCP-compatible AI
systems one shared, persistent memory.** Agents connected to the same memory
can carry knowledge across conversations, hand context to one another, and
react to what another agent learns in real time.

MemoCat is not tied to one model, app, or agent framework. It is a
[Model Context Protocol](https://modelcontextprotocol.io) memory layer backed
by [Montycat](https://montygovernance.com). Run it locally for private memory
across sessions and local clients, or connect multiple machines and systems to
the same trusted Montycat engine for shared memory across agents.

Memories are embedded on-device and recalled by meaning, metadata, timestamp,
or exact key. Montycat keeps the database, vector search, embeddings,
persistent and in-memory storage, live subscriptions, and governance together,
so agents do not need separate vector, embedding, messaging, and policy
services. No cloud embedding API or per-query bill.

## Features

- One persistent memory across conversations, agents, and MCP-compatible systems.
- Shared scopes let multiple agents work from the same facts and project context.
- Semantic vector search with metadata and time-range filtering for RAG.
- Persistent memory, in-memory working spaces, bulk writes, updates, and deletion.
- Real-time memory-change subscriptions without database polling.
- Private scopes, delegated-owner governance, and policy explanations.
- Keyspace lifecycle, semantic-model controls, snapshots, and revocation-safe watch buffers.
- One-command `uvx memocat-mcp` entry point with native/Docker engine bootstrap.

## Why

AI systems forget between conversations, and separate agents cannot naturally
share what they learn. MemoCat gives them a common memory: one agent can store a
decision, another can recall it by meaning, and a third can receive its update
live. Its 23 MCP tools also support exact retrieval, lifecycle management, and
governed deployments, but the core product is the shared memory layer—not a
model-specific plugin or another standalone vector database.

## Install MemoCat MCP

### Claude Code and Cowork plugin

Add the Monty Governance marketplace and install MemoCat from inside Claude
Code:

```text
/plugin marketplace add MontyGovernance/memocat-mcp
/plugin install memocat-mcp@montygovernance
```

The plugin runs the published Python package through `uvx`. Install
[`uv`](https://docs.astral.sh/uv/getting-started/installation/) first, then use
`/mcp` after installation to confirm that the `memocat` server connected. See
the [plugin setup guide](plugins/memocat-mcp/README.md) for engine configuration
and security notes.

### Other MCP clients

The fastest option is [`uvx`](https://docs.astral.sh/uv/guides/tools/), which
runs the latest published package in an isolated environment:

```bash
uvx memocat-mcp
```

If `uvx` is not installed yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uvx memocat-mcp
```

For a persistent command-line installation, use `pipx`:

```bash
pipx install memocat-mcp
memocat-mcp
```

You can also install it into an existing Python environment:

```bash
python -m pip install memocat-mcp
memocat-mcp
```

MemoCat requires Python 3.10 or newer. The package is published as
[`memocat-mcp` on PyPI](https://pypi.org/project/memocat-mcp/).

## Quick start with a Montycat engine

MemoCat reuses a configured Montycat Semantic engine or attempts the supported
native/Docker bootstrap path. For an existing engine:

```bash
export MONTYCAT_URI="montycat://memory-agent:password@localhost:21210/memories"
uvx memocat-mcp
```

## Tools

| Tool | What it does |
|------|--------------|
| `memocat_semantic_search` | Recall by **meaning** (vector kNN), with text or a supplied query vector. |
| `memocat_remember` | Store a fact/record; embedded automatically or indexed with a supplied vector. |
| `memocat_remember_bulk` | Store many memories at once. |
| `memocat_recall` | Fetch by exact key or by field filter. |
| `memocat_list_memories` | Browse / list stored memories (optionally most-recent first). |
| `memocat_update` | Revise a memory in place — memory is mutable. |
| `memocat_forget` | Delete a stored record. |
| `memocat_list_keyspaces` | Discover available memory namespaces. |
| `memocat_create_keyspace` | Provision a namespace; superowners also create a missing configured store in the same engine request. |
| `memocat_remove_keyspace` | Permanently remove an authorized memory namespace with safe watch cleanup. |
| `memocat_enable_semantic` | Enable semantic search and backfill one authorized keyspace. |
| `memocat_enable_external_vectors` | Enroll one keyspace for caller-supplied vectors and a named embedding space. |
| `memocat_semantic_status` | Inspect semantic configuration and backfill state. |
| `memocat_reembed_semantic` | Replace an enrolled text embedding model and backfill the keyspace. |
| `memocat_disable_semantic` | Disable semantic search for one authorized keyspace. |
| `memocat_start_snapshots` | Start scheduled snapshots for one authorized in-memory keyspace. |
| `memocat_stop_snapshots` | Stop scheduled snapshots for one authorized in-memory keyspace. |
| `memocat_clean_snapshots` | Delete snapshot files for one authorized in-memory keyspace. |
| `memocat_policy_view` | View the configured owner's effective governance policy and constraints. |
| `memocat_policy_explain` | Explain whether a proposed governed action is allowed and why. |
| `memocat_policy_history` | View governance history visible to the configured owner. |
| `memocat_await_memory_change` | **Wait for memory to change** — returns the moment another agent or session writes. Live subscription, not polling. |
| `memocat_install_engine` | Install the Montycat engine on this computer and start it. Opens your OS installer and asks for an administrator password, so it only ever runs when you ask for it. |

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

## Montycat Semantic engine requirements

- Python 3.10+ when installing MemoCat through `uv`, `pipx`, or `pip`.
- Access to a **Montycat Semantic** engine. `uvx memocat-mcp` first reuses an
  existing engine, then attempts the supported native/platform installation
  path, and finally falls back to Docker. Semantic search is enabled by default
  in the Semantic edition.

  To start the engine manually with Docker, pick the tag for your CPU—the tag
  carries the architecture:

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

## Docker Compose deployment

Use Compose when you want a reproducible local deployment with a persistent
Semantic engine and an MCP container on the same private Docker network. Docker
is optional when you already manage a reachable Montycat server.

Create a `.env` file beside `compose.yaml`:

```dotenv
MONTYCAT_USERNAME=admin
MONTYCAT_PASSWORD=replace-with-a-strong-password
MONTYCAT_STORE=memories
MEMOCAT_VERSION=0.4.3
# Apple Silicon: arm64-semantic. Intel/AMD64: semantic.
MONTYCAT_IMAGE_TAG=semantic
```

Start the engine and build the MCP image:

```bash
docker compose up -d montycat
docker compose build mcp
```

After the public Docker image is released, use it instead of building from
source:

```bash
docker pull montygovernance/memocat-mcp:0.4.3
```

The Compose service uses `montygovernance/memocat-mcp:${MEMOCAT_VERSION}` and
waits for the Semantic engine health check before launching MCP. The image runs
as an unprivileged `memocat` user and supports both AMD64 and ARM64.

The image installs the released `montycat>=1.2.2,<2` Python client declared in
the package metadata.

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
        "-f", "/absolute/path/to/memocat-mcp/compose.yaml",
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

MemoCat starts serving immediately and acquires an engine in the background, so
the MCP handshake is never held up by a container pull or an embedding-model
download. While that is still in progress, memory tools say so and ask you to
try again in a moment rather than hanging.

The engine may be local or remote. MemoCat first reuses one already reachable
through `MONTYCAT_URI` or the host/port settings — including on another machine
over TCP. If that address is not on this computer and nothing answers, MemoCat
reports it and stops: starting a local engine for a remote address would create
a second database and write memories where you are not looking.

For a local engine that is not running, it tries, in order:

| Step | Route | If it cannot complete |
|---|---|---|
| 1 | Launch an already-installed `montycat_bin` | Docker |
| 2 | Start the `montygovernance/montycat` container | Report how to install |

Before launching a local engine MemoCat asks the `montycat` CLI that ships
beside it (`montycat version`, a compile-time constant that answers while the
engine is down). An installation that cannot run — wrong architecture, missing
ONNX libraries, no execute bit — is skipped immediately rather than launched and
waited on, and the reported edition distinguishes a base-edition install from
the Semantic one the memory tools need. Set `MEMOCAT_ENGINE_CLI` to point at a
CLI in a non-standard location, or `MEMOCAT_ENGINE_BINARY` for the engine
itself.

**Installation is never automatic.** Acquiring the engine opens your operating
system's installer and asks for an administrator password (or, on Linux, runs
the APT setup with `sudo`), which should not happen as a side effect of opening
a chat client. Ask for `memocat_install_engine` instead, and it runs with your
consent:

| Platform | Route |
|---|---|
| macOS Apple Silicon | Discover and download the latest verified `montycat-semantic_<version>_arm64.pkg`, open Installer, and wait for installation (prompts for admin approval) |
| macOS Intel | No Semantic package currently published — use Docker |
| Windows x86_64 | Download verified `.msi` and invoke Windows Installer (prompts for UAC) |
| Linux AMD64 | Run the official one-command APT setup for `montycat-semantic` (prompts for sudo) |
| Other platforms | Use Docker |

MemoCat asks the shared Montycat release catalog for the current Semantic
artifact for macOS or Windows. Artifact URLs are treated as opaque, and the
package's adjacent `.sha256` is required and verified before Installer opens;
verified packages are cached by filename. If catalog discovery is unavailable,
installation stops instead of silently installing an older package.
Override the URL with `MEMOCAT_INSTALLER_URL`, pin a release with
`MEMOCAT_ENGINE_VERSION`, or adjust the Installer completion budget with
`MEMOCAT_INSTALLER_TIMEOUT`. On Linux, set
`MEMOCAT_APT_INSTALL_COMMAND` to use an organization-managed mirror or package
command. ARM64 Linux goes directly to Docker because the official APT repository
is AMD64-only. Set `MEMOCAT_AUTOSTART=off` to disable all start attempts, and
`MEMOCAT_READY_TIMEOUT` (default 20s) to change how long a tool waits for a
starting engine before reporting progress.

## Connect multiple AI systems to one memory

Use the same `MONTYCAT_URI` in each client to give Claude Desktop, Cursor,
OpenAI Codex, and other MCP-compatible systems access to the same memory. A
default local engine shares memory across sessions and local clients on one
machine; cross-machine sharing requires a trusted network-reachable Montycat
engine.

### Claude Desktop

Add MemoCat to `claude_desktop_config.json`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "memocat": {
      "command": "uvx",
      "args": ["memocat-mcp"],
      "env": {
        "MONTYCAT_URI": "montycat://memory-agent:agent-password@localhost:21210/mystore"
      }
    }
  }
}
```

### Cursor

Add the same server definition to your Cursor MCP configuration:

```json
{
  "mcpServers": {
    "memocat": {
      "command": "uvx",
      "args": ["memocat-mcp"],
      "env": {
        "MONTYCAT_URI": "montycat://memory-agent:agent-password@localhost:21210/mystore"
      }
    }
  }
}
```

### OpenAI Codex

Codex can register the local stdio server directly from a terminal:

```bash
codex mcp add memocat \
  --env MONTYCAT_URI="montycat://memory-agent:agent-password@localhost:21210/mystore" \
  -- uvx memocat-mcp
```

Confirm the registration with `codex mcp list`, then start a new Codex session.

### ChatGPT integration

MemoCat currently runs as a local **stdio MCP server**. It works directly with
clients that can launch local MCP commands, including Claude Desktop, Cursor,
and Codex. A ChatGPT connector requires a remotely reachable MCP transport and
cannot connect directly to this stdio command. Remote HTTP transport is not
included in the current package; do not expose the engine's database port as an
MCP endpoint.

## Connect to a remote TLS engine

Keep the normal `montycat://` connection URI and enable TLS separately:

```bash
export MONTYCAT_URI="montycat://memory-agent:agent-password@db.example.com:21210/mystore"
export MONTYCAT_TLS=true
uvx memocat-mcp
```

For a desktop client, add `"MONTYCAT_TLS": "true"` beside `MONTYCAT_URI` in
the server's `env` object. The remote engine must present a certificate trusted
by the machine running MemoCat. Setting `MONTYCAT_URI` disables local engine
auto-install and auto-start because it explicitly selects a managed engine.

## Security and delegated-owner setup

Use a delegated Montycat owner such as `memory-agent` for the MCP process.
Grant that owner only the keyspace read/write and provisioning capabilities its
agent needs. Keep the superowner credential in a separate bootstrap or
governance-administration workflow.

The read-only `memocat_policy_view`, `memocat_policy_explain`, and
`memocat_policy_history` tools expose policy information for the authenticated
owner. They do not accept an owner override and cannot grant, revoke, deny, or
otherwise mutate policy. When automatic keyspace provisioning fails, MemoCat
also requests a read-only policy explanation and appends it to the original
engine error when available.

`memocat_remove_keyspace` is destructive and remains engine-authorized. A
delegated owner may remove a keyspace through creator authority or an explicit
`remove-keyspace` grant unless policy contains an overriding denial. MemoCat
closes active watches and releases resource subscriptions before requesting
removal.

Semantic management is always keyspace-scoped. The MCP server does not expose
database-wide semantic controls; Montycat checks `manage-semantic`, creator
authority, denials, and model allow-lists for every enable or disable request.

Snapshot tools are likewise keyspace-scoped and work only with in-memory
keyspaces. MemoCat does not expose the global snapshot-rate setting. A
`Snapshot rate is not set` response means scheduling has not been configured
on the engine; it is distinct from a governance denial.

Active watches use short authorization leases because the current engine checks
read authority when a subscription opens but does not terminate that connection
after a later revocation. MemoCat revalidates against the engine's filtered
structure view, closes the subscription on access loss, removes MCP resource
ownership, wakes pending callers with an error, and permanently purges buffered
changes so they cannot be replayed after access is restored.

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
| `MONTYCAT_AUTO_PROVISION` | `true` | Auto-create a scope's keyspace on first use. Requires `provision-keyspace` authority for the configured owner and requested storage/model constraints. |
| `MONTYCAT_AUTO_TIMESTAMP` | `true` | Stamp each memory with an indexed `_created_at`, enabling time-range recall (`since`/`until`). Costs a server-side timestamp parse per write — turn off if memories are never recalled by time. |
| `MONTYCAT_SUBSCRIPTION_PORT` | main + 1 | Engine subscription server port (21211 by default; enabled by default) |
| `MONTYCAT_WATCH_BUFFER` | `500` | Changes retained per watched keyspace, so changes between calls aren't lost |
| `MONTYCAT_WATCH_IDLE_TIMEOUT` | `300` | Seconds before an unused subscription is closed |
| `MONTYCAT_WATCH_AUTH_LEASE_SEC` | `5` | Seconds between read-authority checks for active watches. Access loss closes the subscription and purges buffered changes. |
| `MONTYCAT_WATCH_AUTH_TIMEOUT_SEC` | `10` | Maximum seconds allowed for one watch authorization check. A failed check closes the watch safely. |

`memocat_create_keyspace` works with delegated-owner credentials when policy
grants `provision-keyspace` for the requested store, storage type, and semantic
model. The store must already exist for delegated owners. With superowner
credentials, creating the first keyspace also creates a missing configured
store in the same engine request. `memocat_forget` deletes one record and
requires write authority for its keyspace. The engine makes every final
authorization decision.

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
  on first use when the configured owner has provisioning authority.
- **Shared/common memory** — `scope="shared"` → the `MONTYCAT_SHARED_KEYSPACE`.
- **Group memory** — use a group id as the scope (e.g. `scope="team_eng"`).
- **Single-tenant** — set `MONTYCAT_SCOPE` once and omit `scope` per call.

This maps onto Montycat's keyspace governance. In production, run one server
instance per agent or service with delegated-owner credentials and grant only
the provisioning and data authority it needs.

**Isolation note:** `scope` is routing convenience, not authenticated identity.
With one server instance sharing one connection, scopes provide logical
keyspace organization. For credential-enforced isolation, run one server
instance per owner with that owner's delegated credentials; the engine then
denies cross-owner access. Reserve superowner credentials for bootstrap and
governance administration.

## Claude Desktop extension (MCPB)

MemoCat is also packaged as a local Claude Desktop extension. The extension
runs this same stdio MCP server on the user's computer; it is not a hosted MCP
service and does not expose the Montycat database ports as MCP endpoints.

### Install

Install the released `.mcpb` using one of these Claude Desktop methods:

1. Double-click the `.mcpb` file.
2. Drag the file into the Claude Desktop window.
3. Open **Settings > Extensions > Advanced settings > Install Extension** and
   select the file.

Review the requested tools and configure the extension when prompted. Claude
Desktop manages the UV-based Python runtime and installs the dependencies
declared in `pyproject.toml`; a separate Python installation is not required by
the MCPB runtime.

### Extension settings

| Setting | Purpose |
|---|---|
| Existing Montycat URI | Optional sensitive `montycat://user:password@host:port/store` connection. Leave blank for automatic local-engine discovery/setup. |
| Use TLS for existing engine | Enables TLS certificate verification for a configured remote engine. |
| Default memory keyspace | Namespace used when Claude does not specify a scope or keyspace. Defaults to `memory`. |
| Local engine startup mode | `auto` discovers or starts the supported native/Docker engine; `off` requires an already-running engine. |

For a remote engine, use a least-privilege delegated owner and enable TLS. Do
not put a superowner credential into a shared desktop configuration.

### Update and uninstall

Install a newer signed/released MCPB with the same extension name and a higher
version to update it. Removing the extension stops and removes its MCP process,
but deliberately does not erase memory data.

To remove data as well:

- delete individual records or keyspaces before uninstalling when selective
  deletion is desired;
- remove the local MemoCat state directory at `~/.montycat` only when all
  locally managed MemoCat configuration and cached installer state should be
  removed;
- if the engine was started through Docker, remove the `memocat_data` volume
  separately (for example, inspect it first with `docker volume ls` and remove
  that exact volume only when its stored memories are no longer needed);
- for a user-configured remote engine, remove its data using that engine
  operator's process—the desktop extension cannot delete an external
  deployment merely by being uninstalled.

Data deletion is irreversible. Back up required memories before removing a
keyspace, engine data directory, snapshot set, or Docker volume.

### MCPB troubleshooting

- Open the extension details in Claude Desktop Settings and inspect its logs.
- If startup reports that no engine is reachable, start the configured engine,
  correct the URI, or change startup mode from `off` to `auto`.
- If automatic setup cannot install a native engine, install Docker and retry,
  or install Montycat manually and configure its URI.
- On Apple Silicon, use the native `arm64-semantic` engine image; the plain
  `semantic` tag is AMD64.
- For remote TLS failures, verify the hostname and that the certificate is
  trusted by the user's machine. Do not disable TLS merely to bypass a
  certificate error.
- Report reproducible problems at
  https://github.com/MontyGovernance/memocat-mcp/issues or use
  https://montygovernance.com/contact-us.

Maintainers and directory reviewers can use
[MCPB_SUBMISSION.md](MCPB_SUBMISSION.md) for the complete listing copy,
capability declarations, setup procedure, and every-tool verification sequence.

## Privacy Policy

MemoCat processes memory values, search queries, vectors, and configuration
only as needed to perform MCP calls. By default, the MCP process and Montycat
engine run locally, embeddings are generated on-device, and MemoCat includes no
product analytics or telemetry that sends memory contents to Monty Governance.
A user-configured remote engine receives the MCP data sent to that engine and
is governed by its operator's retention and privacy practices.

Persistent memories, snapshots, native engine data, and Docker volumes remain
until the user deletes them; uninstalling the MCPB alone does not erase them.
The complete policy—including collection, storage, sharing, retention,
deletion, third-party distribution services, and contact information—is in
[PRIVACY.md](PRIVACY.md) and is published at
https://github.com/MontyGovernance/memocat-mcp/blob/master/PRIVACY.md.

## Links

- [MemoCat MCP on PyPI](https://pypi.org/project/memocat-mcp/)
- [MemoCat MCP source](https://github.com/MontyGovernance/memocat-mcp)
- [Report an issue](https://github.com/MontyGovernance/memocat-mcp/issues)
- [Changelog](https://github.com/MontyGovernance/memocat-mcp/blob/master/CHANGELOG.md)
- [Montycat documentation](https://montygovernance.com/docs)
- [Montycat Semantic engine on Docker Hub](https://hub.docker.com/r/montygovernance/montycat)
- [Montycat Python client on PyPI](https://pypi.org/project/montycat/)

## License

MIT.
