# Changelog

All notable changes to MemoCat MCP are documented here.

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
