# Zero-config auto-start — plan

**Status:** not built · target release **0.4** · PLAN.md §7.1 / milestone 4

## Why

Installing MemoCat today is four steps: install Docker → `docker run` the engine
→ set `MONTYCAT_URI` → `uvx memocat-mcp`. Competing memory MCP servers are one
step, because they have no database to stand up. Every extra step costs
installs, and a registry listing only gets one first impression.

Goal: `uvx memocat-mcp` works cold, on a machine with no engine, no
configuration, and no prior knowledge of Montycat.

### Fix first, independent of this work

`README.md` tells everyone to run `montygovernance/montycat:semantic`, which is
the **amd64** image. On Apple Silicon that runs under QEMU, which is the exact
configuration where semantic ONNX warm-up segfaults — the reason the `arm64-*`
tags exist (`DOCKERHUB.md:98`). The documented install path fails on the most
common Claude Desktop platform. One-line fix; do it now, not with 0.4.

## Tiers

Tried in order, first success wins. Every tier is skippable and every failure
falls through to the next.

| # | Tier | Status |
|---|------|--------|
| 1 | `MONTYCAT_URI` set, or an engine already listening → just connect | works today |
| 2 | **Native binary** — download, cache under `~/.montycat/bin`, run | needs the artifact below |
| 3 | **Docker** — pull + run the arch-correct image | buildable now |
| 4 | Neither available → one clear error naming both install paths; never hang | — |

Tier 1 must stay first and cheap: an operator who already runs an engine should
never trigger a download.

## Tier 2 — the artifact contract

This is the piece that needs a decision from the engine side, and the reason
this document exists.

**The published installers are the wrong shape for auto-start.** `.pkg`, `.deb`
and `.msi` all need admin rights and write to system paths. A tool launched by
Claude Desktop cannot prompt for sudo. Auto-start needs a **plain binary
archive** it can unpack into the user's own directory.

Proposed contract — same host and naming grammar as the existing downloads
(`downloads.montygovernance.com/{macos,linux,windows}/montycat[-semantic]_<ver>_*`):

```
https://downloads.montygovernance.com/bin/montycat-semantic_<version>_<platform>.tar.gz
https://downloads.montygovernance.com/bin/montycat-semantic_<version>_<platform>.tar.gz.sha256

<platform> ∈ { macos-universal, linux-x86_64, windows-x86_64 }
```

- archive contains `montycat_bin` at its root (plus `libonnxruntime.*` on the
  platforms that need it — the semantic edition links it, see
  SEMANTIC_SEARCH_PLAN.md §9.5)
- `.sha256` is mandatory and verified before first execution
- macOS ships the **universal** binary that already exists in
  `build_script_macos/build/montycat_bin-universal` (x86_64 + arm64), so one
  artifact covers both Macs
- notarization: an unsigned downloaded binary is killed by Gatekeeper. Either
  ship it signed+notarized, or accept that macOS tier 2 will fail on first run
  and fall through to Docker. **Worth deciding before building the artifact** —
  it determines whether tier 2 is useful on Mac at all.

### Until the artifact exists

Tier 2 resolves the URL, gets a 404, logs at debug, and falls through to
Docker. No user-visible failure. To develop and test tier 2 before anything is
published, `MEMOCAT_BINARY_URL` overrides the resolved URL — point it at a
`file://` path or a local HTTP server and the whole tier is exercisable today.

That override is also the escape hatch for air-gapped installs.

## Tier 3 — Docker

```python
tag = "arm64-semantic" if platform.machine() in ("arm64", "aarch64") else "semantic"
docker run -d --name memocat-engine \
  -p 21210:21210 -p 21211:21211 \
  -e MONTYCAT_SUPEROWNER=<generated> -e MONTYCAT_PASSWORD=<generated> \
  -v montycat_data:/var/lib/.montycat \
  montygovernance/montycat:<tag>
```

Port 21211 is not optional — the real-time watch surface is useless without the
subscription server (`watch.py`).

## Implementation

New module `memocat_mcp/bootstrap.py`, called once from `_get_engine()` before
the first connection:

```python
async def ensure_engine() -> EngineTarget   # host, port, credentials
```

Pieces:

- **`probe(host, port)`** — TCP connect with a short timeout. Tier 1, and the
  readiness check for tiers 2 and 3.
- **`resolve_binary_url()`** — platform → URL, or None. Honours
  `MEMOCAT_BINARY_URL`.
- **`fetch_binary()`** — download to a temp file, verify sha256, unpack into
  `~/.montycat/bin/<version>/`, `chmod +x`. Never overwrite an existing verified
  copy; a cached binary means the second start is instant.
- **`start_native()` / `start_docker()`** — spawn, then wait for readiness.
- **`credentials()`** — generate a superowner/password on first run, persist to
  `~/.montycat/memocat.json` (mode 0600), reuse thereafter. Auto-provisioning of
  per-scope keyspaces needs superowner, so this cannot be skipped.

### Decisions to make explicit in code

- **Readiness deadline.** First semantic start downloads an embedding model
  (~24–90 MB) before it serves. A 5-second wait would declare failure on a
  working engine. Budget ~120 s for a cold start, ~15 s for a warm one, and emit
  progress rather than blocking silently.
- **Lifetime.** The engine must **outlive the MCP process** — this is a memory
  product; killing the database when the agent restarts would look like amnesia.
  Leave it running, reuse it next launch, and document how to stop it. This is
  the opposite of the usual "clean up your subprocess" instinct and needs to be
  deliberate.
- **Concurrency.** Two agents may launch MemoCat simultaneously. Guard start
  with a lockfile in `~/.montycat/`, and treat "port already in use" as success
  after re-probing, not as an error.
- **Never hang.** Every tier is bounded. Tier 4's message must name both install
  paths and the exact env var to skip all of this.

## Config

| Variable | Default | Purpose |
|---|---|---|
| `MEMOCAT_AUTOSTART` | `auto` | `auto` \| `off` \| `native` \| `docker` — pin a tier or disable |
| `MEMOCAT_BINARY_URL` | — | Override the tier-2 artifact URL (dev, air-gapped) |
| `MEMOCAT_ENGINE_TIMEOUT` | `120` | Seconds to wait for readiness on a cold start |
| `MONTYCAT_URI` | — | Set → tier 1 only; auto-start never runs |

## Verification

- **Unit (CI, no engine, no Docker):** URL resolution per platform, checksum
  rejection, tier fallthrough order, `MEMOCAT_AUTOSTART=off` short-circuit,
  timeout bounds. Mock the process launches.
- **Live:** with an engine already up, tier 1 wins and nothing is downloaded or
  started — assert no container is created. With Docker available and no engine,
  tier 3 brings one up and the existing 54-test suite passes against it
  unchanged.
- **Cold-start matrix**, manually at least once per platform: Apple Silicon,
  Intel Mac, Linux x86_64, Windows. The arm64 tag selection is the specific
  thing to confirm on Apple Silicon.
- **Second launch** must be materially faster and must not re-download.

## Phasing

1. README tag fix (now, standalone)
2. Tiers 1 + 3 + 4 with the arch-correct tag — this alone turns four steps into
   one for anyone with Docker, and unblocks the discoverability blitz
3. Tier 2 once the binary archive is published; `MEMOCAT_BINARY_URL` lets it be
   written and tested before then
4. Drop the Docker requirement from the README's Requirements section, and lead
   with `uvx memocat-mcp`

Sequencing note: the discoverability blitz (PLAN.md milestone 5) should follow
step 2, not precede it. Driving a registry listing at a four-step manual install
spends the one first impression you get.
