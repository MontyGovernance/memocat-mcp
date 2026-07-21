# MemoCat — MCP Server for Montycat — Build & Discoverability Plan

**Package:** `memocat-mcp` · **Language:** Python (FastMCP) · **Status:** v0.3 built & validated live

> **Done so far:** 9 tools (semantic_search, remember, remember_bulk, recall,
> list_memories, update, forget, list_keyspaces, create_keyspace), multi-tenant
> `scope` layer (per-owner `mem_<scope>` keyspaces + `shared`, auto-provisioned),
> persistent/in-memory auto-detection. All validated end-to-end against a live
> Semantic engine (isolation proven: per-scope semantic recall, zero cross-tenant
> leakage). Not yet published.

An MCP (Model Context Protocol) server that exposes Montycat to LLM agents
(Claude Desktop, Cursor, ChatGPT desktop, agent frameworks) as callable tools —
turning Montycat into **native, semantically-searchable long-term memory for AI
agents**. This is the flagship "AI memory, native" story made real, and it
unblocks four otherwise-blocked agent-discovery items on the website.

---

## 1. Decision: Python + FastMCP (and why, for discoverability & adoption)

Both official SDKs (TypeScript, Python) are first-class, and the Montycat
clients are at **full API parity** — either wraps equally well. So the choice
rests on reaching and converting the audience, not on the client:

- **Audience fit** — the people who install an MCP server for a vector/AI
  database (RAG, agents, LLM apps) skew Python. Ship where they already work.
- **Ecosystem convention** — FastMCP is the dominant authoring framework in the
  MCP-server population; registries and awesome-lists are full of it, so the
  package reads as idiomatic to reviewers and users.
- **Distribution parity** — `uvx memocat-mcp` is as frictionless as `npx`, and
  `uv` erased Python's historical env/install pain. No disadvantage vs TS.
- **No channel lock-out** — PyPI, the official MCP registry, the Docker MCP
  catalog, Smithery/Glama/mcp.so/PulseMCP all accept Python servers.

TS's only remaining edge (reference SDK tracks spec first) does not matter for a
database-tools server. **Python wins on adoption; discoverability is a wash, so
Python.**

> Phase 2 option (not now): bake MCP directly into the engine binary
> (`montycat_bin` speaks Streamable HTTP MCP natively via the Rust `rmcp` SDK).
> That is a first-class-feature move; the standalone Python adapter ships first.

---

## 2. Architecture

Thin adapter. No business logic — it translates MCP tool calls into calls on the
`montycat` PyPI client and returns structured results.

```
Agent (Claude Desktop / Cursor / framework)
  ⇅  MCP  (stdio locally · Streamable HTTP for remote/hosted)
memocat-mcp  (FastMCP server)
  ⇅  montycat PyPI client
Montycat engine (Semantic edition — vector search on)
```

- **Transport:** `stdio` for local agents (the default install path). Optional
  **Streamable HTTP** mode for a hosted/shared server later.
- **Connection:** one `Engine` built from `MONTYCAT_URI` (or discrete host/port/
  user/pass/store env vars). Superowner creds for admin tools.
- **Dynamic keyspaces:** the client is class-based
  (`class X(Keyspace.Persistent)`). The MCP server takes a `keyspace` string and
  binds a keyspace object on the fly (build the subclass via `type(...)` with the
  `keyspace` attribute + `connect_engine(engine)`, cached per name). This is the
  one real implementation detail to nail — MCP tools are string-parameterized,
  the client is class-defined.

---

## 3. Tools (v1)

Map to verified client methods. Keep names agent-legible; lead with the memory story.

| Tool | Wraps | Purpose |
|------|-------|---------|
| `memocat_semantic_search` | `semantic_search_get_values(...)` / `semantic_search_get_values_where(...)` | Retrieve by meaning (kNN). Returns scored `{key, score, value}`. The core RAG/recall tool. Hybrid: `filters` / `since` / `until` restrict which memories are ranked (hard AND; ranking stays pure similarity). |
| `memocat_remember` | `insert_value(value)` / `insert_custom_key_value` | Store a fact/record; it is embedded + indexed automatically. |
| `memocat_recall` | `get_value(key/custom_key)` / `lookup_values_where(...)` | Fetch by key or by field filter (exact recall). |
| `memocat_list_keyspaces` | `get_structure_available()` | Discover available memory stores/keyspaces. |
| `memocat_create_keyspace` | `create_keyspace()` (persistent/in-memory) | Provision a new memory namespace. |
| `memocat_forget` | `delete_key(key/custom_key)` | Remove a stored record. |

Each tool: clear description (this is what the LLM reads to decide when to call
it), JSON-Schema inputs, returns JSON text content. Errors surfaced as tool
errors, not swallowed.

Resources (optional, phase 1.5): expose keyspace structure as an MCP *resource*
so agents can browse available memory without a tool call.

---

## 4. Package & distribution

- `pyproject.toml` — name `memocat-mcp`, entry point `memocat-mcp = "memocat_mcp.server:main"`, dep `mcp[cli]` (FastMCP) + `montycat`. `uvx`-ready.
- **PyPI** publish (`memocat-mcp`).
- **`uvx memocat-mcp`** — the primary install path.
- **Docker image** — Montycat is already Docker-native; ship
  `montygovernance/memocat-mcp` for the Docker MCP catalog + `docker mcp` users.
- **Claude Desktop / client config** in README:
  ```json
  {
    "mcpServers": {
      "memocat": {
        "command": "uvx",
        "args": ["memocat-mcp"],
        "env": { "MONTYCAT_URI": "montycat://admin:pass@localhost:21210/store" }
      }
    }
  }
  ```

---

## 5. Discoverability (the whole point)

Get listed everywhere agents and humans look for MCP servers, and wire the
website into it:

- **Official MCP registry** — submit `memocat-mcp`.
- **Docker MCP catalog / registry** — submit the image (files drafted per SEO_PLAN).
- **awesome-mcp-servers** — PR under databases / memory / vector.
- **Directories** — Smithery, Glama, mcp.so, PulseMCP listings.
- **Website `.well-known` (unblocks AI_PLAN items):**
  - `/.well-known/mcp/server-card.json` (SEP-1649): serverInfo + transport + capabilities.
  - `/.well-known/agent-skills/index.json`: skills array referencing the server, each with a sha256.
  - RFC 8288 `Link` headers pointing at both (extend `next.config.mjs`).
- **Cross-link** from the site: an `/mcp` landing page (AI-category cluster) +
  mentions on `/ai-memory` and `/rag`. Same additive SEO strategy — new surface,
  homepage untouched.
- **llms.txt / llms-full.txt** — add the MCP server + install one-liner.

---

## 6. README / copy (SEO + voice)

- Keyword-optimized title/first-paragraph: "MCP server for Montycat — give AI
  agents self-hosted, semantically-searchable long-term memory (vector search,
  RAG) in a NoSQL + vector database."
- Brand voice per [[montycat-brand-voice]]: Trotsky 80 / GG 20 in the prose,
  keywords disciplined in the H1 / first line / headings.
- Backlink to montygovernance.com, docs, and the client packages.

---

## 7. The product story — three killer features (adoption plan)

Ranked by adoption impact. The pitch they add up to: *"install in one command,
memory that fades like real memory, and agents that notice when it changes."*

### 7.1 Zero-config auto-start — THE adoption lever
> Detailed design: **[AUTOSTART_PLAN.md](AUTOSTART_PLAN.md)** — tiers, the
> binary-artifact contract the engine side needs to publish, and the
> README tag bug that breaks Apple Silicon installs today.

`uvx memocat-mcp` must work cold, with no pre-existing engine. Tiered:
1. `MONTYCAT_URI` set / engine reachable → just connect (skip everything).
2. **Native binary** → download prebuilt `montycat_bin` for the platform into
   `~/.montycat/bin` (base engine is static musl on Linux; Windows binary
   exists), cache, start, connect.
3. **Docker fallback** → pull/run the arch-correct tag:
   `arm64-semantic` on Apple Silicon, `semantic` on x86_64. Picking the
   wrong one runs amd64 under emulation, where semantic warm-up crashes.
4. Neither → clear error with the two install paths; never hang.

Known ceiling: **macOS has no native engine build yet** → Mac users are
Docker-gated until one ships. A native mac build (or per-platform pip wheels
carrying the engine, the `uv`/`ruff` trick) is the single biggest widening of
the funnel — engine work, tracked here as a dependency.

### 7.2 Fading memory (short-term vs long-term) — the marketing gold
Already in the engine; must be productized:
- **Long-term memory** = persistent keyspaces (default today).
- **Working memory** = in-memory keyspaces + `expire_sec` → *memory that fades*.
- `ttl` framing on `memocat_remember` — **still open, and deliberately not a
  parameter today.** `expire_sec` was removed from `remember`/`update`/
  `remember_bulk` (2026-07-20). Reason: expiry is an *in-memory-tier*
  capability — the engine's timer is in-memory only, and every client encodes
  that structurally (`persistent_kv`/`KeyspacePersistent`/`persistent.rs` have
  no such parameter at all; only the in-memory classes do). MCP collapses both
  tiers into one tool, so it loses that guarantee for free, and the MCP default
  tier is persistent. A parameter an LLM can see in the tool schema is one it
  will use — a docstring caveat does not undo a schema field. Reintroduce it
  only together with working-memory routing (TTL writes land in an in-memory
  keyspace), never as a bare parameter on a persistent-by-default tool.
- ~~Auto-stamp `_created_at` on every remember~~ ✅ **done** — `_stamp()` adds a
  UTC ISO-8601 `_created_at` on `remember`/`remember_bulk` unless the caller
  supplied one (historical imports keep their own timestamps, hoisted so they
  are range-queryable too).
  **Wire detail worth remembering:** the engine only puts a field in the
  *timestamp* index if it arrives nested under a `timestamps` object — it
  parses those, then flattens them back to the top level of the stored value.
  A top-level date string is an ordinary kv string: exact-match only, so
  `since`/`until` silently return nothing. It costs a server-side parse per
  write, so it is opt-out: `MONTYCAT_AUTO_TIMESTAMP=false` globally or
  `timestamp=False` per call.
- ~~"what did we discuss yesterday" time-range recall~~ ✅ **done, and it merged
  with search rather than living beside it.** Engine Phase 3 (hybrid metadata
  pre-filter, `semantic_filter`) means the timestamp stamp is queryable through
  the *same* call as meaning: `memocat_semantic_search(query, since=…,
  until=…, filters={…})`. Semantic + time window + metadata in one tool. The
  original plan filed time-range recall as a separate capability; it isn't one.
- Headline: **"the only agent memory with a forgetting curve."** — now backed by
  two mechanisms, not one: TTL (memory that expires) *and* time-scoped recall
  (memory retrieved by when it was formed).

### 7.3 Real-time memory watch — the differentiator nobody has — DONE (2026-07-20)
Montycat has native live subscriptions; exposed via MCP through **two surfaces
over one subscription** (`memocat_mcp/watch.py`):

- **`memocat_await_memory_change` tool** — long-poll backed by the live
  subscription: sleeps until a write lands, returns in milliseconds with
  `{seq, key, event, value}`. This is the surface that actually works, because
  most MCP clients today do *not* surface resource updates to the model. A
  `since_seq` cursor resumes exactly where the agent left off, so changes
  between calls are buffered rather than missed.
- **MCP resources** — `montycat://memory/{keyspace}`, readable, with
  `notifications/resources/updated` pushed to clients that implement resource
  subscriptions. Also ticks the Resources box registries look for (§8).

Implementation notes worth keeping:
- **The SDK hardcodes `subscribe=False`** in the resources capability
  (`mcp/server/lowlevel/server.py`) even when subscribe handlers are
  registered, so `mcp.run()` would advertise no subscription support and no
  client would ever subscribe. `main()` therefore builds the
  `InitializationOptions` itself, flips `capabilities.resources.subscribe`, and
  runs the SDK's own stdio path. Verified over a real client handshake:
  `resources -> subscribe=True listChanged=True`.
- The subscription callback runs **inline in the client's read loop**, so it
  only parses, buffers, and schedules — never awaits.
- **Teardown is load-bearing:** a lingering engine subscription keeps sled
  subscribers alive and deadlocks later `remove_keyspace`/`remove_store`. Every
  exit path stops the subscription; the E2E asserts `remove_keyspace` completes
  promptly afterwards as a deadlock canary.
- Subscriptions open on demand, close when idle — zero cost for users who never
  watch.
- **No engine changes were needed**: the subscription server is on by default
  (`DEFAULT_ALLOWED = vec![0, 1]`, port 21211) and change frames already carry
  key, value, and event.

---

## 8. Polish (cheap, spec-visible)

- **MCP Resources** — expose stores/keyspaces structure as browsable resources
  (registries look for resource support).
- **MCP Prompts** — ship a "use your memory" prompt template (when to remember,
  when to search, when to update) → instant Claude Desktop UX.
- **Streamable HTTP mode + Docker image** (`montygovernance/memocat-mcp`) —
  hosted/shared team memory; unlocks the Docker MCP catalog.
- README **demo GIF** — agent remembering across restarts; listings with GIFs convert.

---

## 9. Milestones

1. ~~**Scaffold + v1 tools**~~ ✅ done (9 tools, scoping, auto-detect, validated live).
2. **Memory-complete polish** — mostly done: ~~`_created_at` auto-timestamp~~ ✅,
   ~~time-range recall~~ ✅ (folded into hybrid search, §7.2), plus **hybrid
   metadata filtering** (`filters=` on `memocat_semantic_search`, not in the
   original plan — arrived with engine Phase 3). Remaining: `ttl` framing.
   Requires `montycat>=1.0.7` (the `_where` client methods) and a Montycat
   Semantic engine >= 1.2.3; older engines silently ignore the filter.
3. **Publish** — PyPI (`uvx memocat-mcp`) + Docker image. The stdio "deploy."
4. **Zero-config auto-start** (§7.1) — tiered engine bootstrap. Ship as 0.4
   (0.3 went to the real-time watch). Designed in
   [AUTOSTART_PLAN.md](AUTOSTART_PLAN.md); tiers 1+3 are buildable now, tier 2
   waits on a user-space binary archive (the `.pkg`/`.deb`/`.msi` installers
   need admin rights and cannot be used unattended).
5. **Discoverability blitz** — official MCP registry, Docker MCP catalog,
   awesome-mcp-servers PR, Smithery/Glama/mcp.so/PulseMCP + website wiring
   (`.well-known` card, agent-skills index, Link headers, `/mcp` landing page,
   llms.txt/llms-full entries). Card ships only after PyPI is live.
6. ~~**Real-time watch** (§7.3)~~ ✅ **done 2026-07-20** — subscriptions → MCP
   notifications + `memocat_await_memory_change`. Verified live: a waiting
   agent wakes ~30 ms after another session's write; inserts, updates and
   deletes all delivered; buffered across calls; subscriptions released cleanly
   (deadlock canary green). Ships as 0.3 — **the two-agent demo is the launch
   asset and still needs recording** (§8 GIF).
7. **Resources + Prompts + HTTP mode** (§8) — Resources ✅ done with §7.3
   (`montycat://memory/{keyspace}`, subscribable). Prompts + HTTP mode open.
8. **Engine dependency** — native macOS build (or engine-in-wheel packaging) to
   un-gate Mac cold installs.

---

## 10. Decisions taken

- Server name in configs: **`montycat`**.
- Default keyspace: **`memory`, auto-provisioned**; scoping via `scope` →
  `mem_<scope>` (+ `shared`); `MONTYCAT_SCOPE` env for single-tenant setups.
- Storage default: **persistent** (long-term memory); in-memory + TTL = working
  memory (§7.2). Existing keyspaces auto-detected, env only decides new ones.
- Admin surface: `create_keyspace`/`forget` require superowner creds; isolation
  is logical per-keyspace with one shared connection, credential-enforced when
  running one server instance per owner (documented in README).
