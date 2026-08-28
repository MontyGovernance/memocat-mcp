# Changelog

All notable changes to MemoCat MCP are documented here.

## 0.4.3 — 2026-08-28

### Added

- Add an MCPB v0.4 Claude Desktop extension using the cross-platform UV
  runtime, generated settings UI, privacy policy, and reproducible packaging.
- Add human-readable titles and explicit read-only, mutating, and destructive
  MCP annotations for all 23 tools.
- Add release gates that synchronize package/manifest versions and require a
  512×512 transparent directory icon.
- Add `memocat_install_engine`, which downloads the Montycat package and opens
  the operating system's installer. Engine installation is no longer something
  that can happen without being asked for.
- Verify a local installation with `montycat version` before launching it. The
  CLI ships beside the engine in every packaging and prints a compile-time
  constant, so it answers while the engine is down. A binary that cannot run —
  wrong architecture, unresolvable ONNX libraries, no execute bit — is now
  detected in milliseconds instead of after the full readiness budget, and the
  reported edition distinguishes a base-edition install from the Semantic one
  the memory tools require. `MEMOCAT_ENGINE_CLI` overrides its location.

### Changed

- Serve MCP immediately and acquire the engine in the background. Startup
  previously awaited engine readiness before opening the transport, so a first
  run that had to install or pull an engine could take minutes — far longer
  than a client waits to complete `initialize`. Tools now report that the
  engine is still starting instead of the connection appearing to fail.
  `MEMOCAT_READY_TIMEOUT` (default 20s) bounds that wait.
- Never start a local engine for an address on another machine. `MONTYCAT_HOST`
  can name a remote engine, but the native and Docker tiers only bind locally;
  they previously started an engine nobody was watching, waited out the full
  readiness budget twice against an address that could not answer, and left a
  stray container behind.

### Fixed

- Send an identifying `User-Agent` on release-catalog and artifact requests.
  Both hosts answer 403 to urllib's default `Python-urllib/x.y`, so installer
  discovery and every download failed closed and were swallowed into a debug
  log — leaving machines without Docker unable to obtain an engine at all.
- Restore saved credentials when reconnecting to a previously managed local
  Montycat engine, so a new Claude Desktop session can use the existing engine.
- Fall back correctly when a generated extension setting arrives empty rather
  than absent, so a cleared keyspace or startup-mode field uses its default.
- Stop advising `MEMOCAT_AUTOSTART=off` in the error raised *because* autostart
  is off.

## 0.4.2 — 2026-08-27

### Added

- Add the official MCP Registry ownership marker and `server.json` manifest for
  `io.github.MontyGovernance/memocat-mcp`.
- Prepare a multi-architecture Docker Hub distribution with OCI metadata and
  the official MCP Registry ownership label.
- Run the MCP image as an unprivileged user and wait for the Compose Semantic
  engine to become healthy before starting MCP.

## 0.4.1 — 2026-08-27

### Fixed

- Pass `semantic=False` through keyspace creation instead of accidentally
  inheriting the Python client's `semantic=True` default.

## 0.4.0

First public release.

### Added

- Nineteen MCP tools for semantic memory, exact recall, bulk operations,
  keyspace lifecycle, governance, semantic management, snapshots, and live
  memory-change watches.
- Hybrid semantic retrieval with metadata and indexed time-range filtering.
- Automatic `_created_at` timestamps for temporal agent memory.
- Persistent, in-memory, private-scope, and shared-scope memory routing.
- MCP resources with live `resources/updated` notifications.
- Delegated-owner policy view, explanation, history, provisioning, removal,
  semantic management, and snapshot management.
- Revocation-aware authorization leases that close watches and purge buffered
  data after access loss.
- Native engine discovery and verified platform-package bootstrap, with Docker
  fallback.
- Precomputed-vector memory writes and queries, external-vector profile
  enrollment, semantic status inspection, and semantic re-embedding.

### Security

- Engine-enforced cross-owner isolation and a 24-scenario live governance
  acceptance matrix.
- Safe keyspace removal that releases subscriptions before engine teardown.
- Mandatory SHA-256 verification for downloaded engine packages.
