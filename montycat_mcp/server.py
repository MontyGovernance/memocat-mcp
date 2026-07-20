"""Montycat MCP server.

Exposes a Montycat engine to LLM agents as MCP tools, turning it into
self-hosted, semantically-searchable long-term memory: agents store facts and
recall them by meaning (vector search) or by key, all on your own hardware.

Connection is configured from the environment:

    MONTYCAT_URI           montycat://user:pass@host:port/store   (preferred)

  or the discrete parts:

    MONTYCAT_HOST          default 127.0.0.1
    MONTYCAT_PORT          default 21210
    MONTYCAT_USERNAME
    MONTYCAT_PASSWORD
    MONTYCAT_STORE
    MONTYCAT_TLS           "true"/"false", default false

  behavior:

    MONTYCAT_DEFAULT_KEYSPACE   default "memory" — used when a tool omits scope/keyspace
    MONTYCAT_PERSISTENT         "true"/"false", default true — new keyspaces persist
    MONTYCAT_SCOPE              default owner/scope, used when a tool omits `scope`
    MONTYCAT_SCOPE_PREFIX       default "mem_" — per-owner keyspace prefix
    MONTYCAT_SHARED_KEYSPACE    default "mem_shared" — the common/shared keyspace
    MONTYCAT_AUTO_PROVISION     "true"/"false", default true — create a scope's
                                keyspace on first use (needs superowner creds)
    MONTYCAT_AUTO_TIMESTAMP     "true"/"false", default true — stamp each memory
                                with an indexed `_created_at`, enabling
                                time-range recall (`since`/`until`). Costs a
                                server-side timestamp parse per write; turn off
                                if memories are never recalled by time.

Scoping: pass `scope` (an owner/user id) to any memory tool and it targets that
owner's private keyspace `mem_<scope>` — isolated semantic recall per owner. The
special scope "shared" targets the common keyspace all owners can use. Omit scope
to use MONTYCAT_SCOPE, then the default keyspace. Isolation is by keyspace, which
also aligns with Montycat's per-keyspace RBAC (grant owners access to their own).

Semantic search requires the Montycat **Semantic** edition (it is enabled there
by default).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from montycat import Engine, Keyspace, Timestamp

mcp = FastMCP("montycat")

_engine: Optional[Engine] = None
_keyspaces: dict[tuple[str, bool], Any] = {}
# name -> is-persistent, learned from the engine's structure (or recorded on create)
_ks_type_cache: dict[str, bool] = {}


def _now_iso() -> str:
    """UTC wall clock as the ISO-8601 shape the engine's timestamp index
    parses (`2026-07-20T14:30:00`). Every remember stamps `_created_at` with
    this, which is what makes time-range recall (`since`/`until`) work."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _is_indexable_timestamp(text: Any) -> bool:
    """Whether the engine's timestamp parser will accept this string. Guards
    the hoist below: a `timestamps` entry the engine cannot parse fails the
    whole write, so anything unrecognized is left as an ordinary field."""
    if not isinstance(text, str):
        return False
    try:
        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def _stamp(value: dict, enabled: Optional[bool] = None) -> dict:
    """Give a memory an indexed `_created_at`, unless timestamping is off.

    The engine only puts a field in its *timestamp* index if it arrives nested
    under a `timestamps` object; it parses those, then flattens them back to
    the top level of the stored value. A plain top-level date string is just a
    string in the kv index — matchable by exact equality, never by range — so
    `since`/`until` recall would silently return nothing.

    Timestamping costs a server-side regex/parse sweep per write, so it can be
    turned off (`MONTYCAT_AUTO_TIMESTAMP=false`, or `timestamp=False` on a
    single call) when memories are never recalled by time. With it off,
    `since`/`until` have nothing to match — the trade is explicit.

    A caller-supplied `_created_at` (historical import) is hoisted into
    `timestamps` so it is range-queryable too, but only when it parses: an
    unparseable entry there would fail the whole insert.
    """
    if enabled is None:
        enabled = _env_bool("MONTYCAT_AUTO_TIMESTAMP", True)
    if not enabled:
        return value

    value = {**value}
    stamps = dict(value.get("timestamps") or {})

    supplied = value.get("_created_at")
    if supplied is not None and "_created_at" not in stamps:
        if not _is_indexable_timestamp(supplied):
            return value  # unparseable — leave the caller's value untouched
        stamps["_created_at"] = value.pop("_created_at")

    stamps.setdefault("_created_at", _now_iso())
    value["timestamps"] = stamps
    return value


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_engine() -> Engine:
    """Build the shared Engine once, from the environment."""
    global _engine
    if _engine is not None:
        return _engine

    uri = os.environ.get("MONTYCAT_URI")
    if uri:
        _engine = Engine.from_uri(uri)
    else:
        _engine = Engine(
            host=os.environ.get("MONTYCAT_HOST", "127.0.0.1"),
            port=int(os.environ.get("MONTYCAT_PORT", "21210")),
            username=os.environ.get("MONTYCAT_USERNAME", ""),
            password=os.environ.get("MONTYCAT_PASSWORD", ""),
            store=os.environ.get("MONTYCAT_STORE"),
            tls=_env_bool("MONTYCAT_TLS", False),
        )
    return _engine


def _default_keyspace() -> str:
    return os.environ.get("MONTYCAT_DEFAULT_KEYSPACE", "memory")


def _scope_prefix() -> str:
    return os.environ.get("MONTYCAT_SCOPE_PREFIX", "mem_")


def _shared_keyspace() -> str:
    return os.environ.get("MONTYCAT_SHARED_KEYSPACE", "mem_shared")


def _resolve_keyspace(scope: Optional[str] = None, keyspace: Optional[str] = None) -> str:
    """Turn a scope/keyspace into the concrete keyspace name to target.

    Precedence:
      1. explicit `keyspace`      -> used verbatim (escape hatch)
      2. `scope` (or MONTYCAT_SCOPE env) -> per-owner keyspace `mem_<scope>`;
         the special scope `"shared"` maps to the common keyspace
      3. neither                  -> the default keyspace
    """
    if keyspace:
        return keyspace
    scope = scope or os.environ.get("MONTYCAT_SCOPE")
    if scope:
        if scope == "shared" or scope == _shared_keyspace():
            return _shared_keyspace()
        return f"{_scope_prefix()}{scope}"
    return _default_keyspace()


def _keyspace(name: Optional[str], persistent: Optional[bool] = None):
    """Bind a Montycat keyspace class by name (built and cached on first use).

    The Montycat client models keyspaces as classes; MCP tools pass a string, so
    we synthesize the subclass on the fly and connect it to the shared engine.
    """
    name = name or _default_keyspace()
    if persistent is None:
        persistent = _env_bool("MONTYCAT_PERSISTENT", True)

    cache_key = (name, persistent)
    cached = _keyspaces.get(cache_key)
    if cached is not None:
        return cached

    base = Keyspace.Persistent if persistent else Keyspace.InMemory
    cls = type(name, (base,), {"keyspace": name})
    cls.connect_engine(_get_engine())
    _keyspaces[cache_key] = cls
    return cls


async def _resolve_persistent(name: str) -> Optional[bool]:
    """Detect whether an existing keyspace is persistent (True) or in-memory
    (False) from the engine's structure. Returns None if it does not exist yet.
    Result is cached per name."""
    if name in _ks_type_cache:
        return _ks_type_cache[name]
    try:
        res = await _get_engine().get_structure_available()
    except Exception:
        return None
    payload = res.get("payload") if isinstance(res, dict) else None
    structure = (payload or {}).get("structure") or {}
    for store in structure.values():
        if not isinstance(store, dict):
            continue
        if name in (store.get("persistent") or {}):
            _ks_type_cache[name] = True
            return True
        if name in (store.get("inmemory") or {}):
            _ks_type_cache[name] = False
            return False
    return None


async def _ensure_keyspace(name: str, persistent: bool) -> None:
    """Create a keyspace if it does not exist (best-effort; needs superowner).

    Enables auto-provisioning of per-owner scopes on first use. Failures (e.g.
    non-superowner credentials, or a race where it already exists) are ignored —
    the subsequent operation surfaces any real error.
    """
    try:
        ks = _keyspace(name, persistent=persistent)
        await ks.create_keyspace()
        _ks_type_cache[name] = persistent
    except Exception:
        pass


async def _bind(name: Optional[str] = None, persistent: Optional[bool] = None):
    """Resolve and bind a keyspace, auto-detecting its persistent/in-memory type
    and auto-provisioning it on first use (per-owner scopes).

    Precedence for type: explicit `persistent` arg > the keyspace's actual type
    on the engine (self-correcting) > the `MONTYCAT_PERSISTENT` env default.
    A keyspace that does not exist yet is created when MONTYCAT_AUTO_PROVISION
    is enabled (default true).
    """
    name = name or _default_keyspace()
    if persistent is None:
        detected = await _resolve_persistent(name)
        if detected is None:
            persistent = _env_bool("MONTYCAT_PERSISTENT", True)
            if _env_bool("MONTYCAT_AUTO_PROVISION", True):
                await _ensure_keyspace(name, persistent)
        else:
            persistent = detected
    return _keyspace(name, persistent=persistent)


# ── tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def montycat_semantic_search(
    query: str,
    keyspace: Optional[str] = None,
    scope: Optional[str] = None,
    limit: int = 5,
    min_score: Optional[float] = None,
    filters: Optional[dict] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Any:
    """Search stored memory by MEANING (vector / semantic search), not keywords.

    Use this to recall relevant facts, documents, or past context for RAG and
    agent memory. Returns the top matches ranked by similarity, each with its
    key, a cosine-similarity score, and the stored value.

    Hybrid mode: `filters`, `since`, and `until` restrict WHICH memories are
    ranked — a hard AND constraint over indexed fields; ranking stays pure
    similarity. Combine them freely: "what did we decide about the index"
    + `since` yesterday + `filters={"project": "montycat"}` is one call.
    A filter matching nothing returns []. Requires a Montycat Semantic engine
    with hybrid support (>= 1.2.3); older engines ignore the filter.

    Args:
        query: Natural-language description of what to recall.
        scope: Owner/user id to scope recall to (searches only that owner's memory,
               keyspace mem_<scope>). Use "shared" for the common keyspace.
        keyspace: Explicit keyspace override (advanced; bypasses scope).
        limit: Max number of results (default 5).
        min_score: Optional similarity floor in [-1, 1]; drops weak matches.
        filters: Optional metadata constraints, e.g. {"project": "x"} — only
                 memories whose indexed fields equal these values are ranked.
        since: Only memories created at/after this time (ISO-8601, UTC —
               matches the auto-stamped `_created_at`).
        until: Only memories created before this time (ISO-8601, UTC).
    """
    ks = await _bind(_resolve_keyspace(scope, keyspace))
    if since or until:
        filters = dict(filters or {})
        if since and until:
            filters["_created_at"] = Timestamp(start=since, end=until)
        elif since:
            filters["_created_at"] = Timestamp(after=since)
        else:
            filters["_created_at"] = Timestamp(before=until)
    if filters:
        return await ks.semantic_search_get_values_where(
            query, filters, limit=limit, min_score=min_score
        )
    return await ks.semantic_search_get_values(query, limit=limit, min_score=min_score)


@mcp.tool()
async def montycat_remember(
    value: dict,
    keyspace: Optional[str] = None,
    scope: Optional[str] = None,
    custom_key: Optional[str] = None,
    expire_sec: int = 0,
    timestamp: Optional[bool] = None,
) -> Any:
    """Store a fact or record in memory; it is embedded and indexed automatically.

    Later recall it by meaning with montycat_semantic_search, or by key with
    montycat_recall. Returns the generated key in `payload`.

    Every record is auto-stamped with an indexed `_created_at` (UTC ISO-8601)
    unless the value already carries one — this powers time-range recall
    (`since`/`until` on montycat_semantic_search). Top-level fields are
    indexed, so they can be used as `filters` in hybrid search (e.g. store
    `{"project": "x", ...}`, later filter on it).

    Args:
        value: The record to store (a JSON object).
        scope: Owner/user id to store under (that owner's private memory,
               keyspace mem_<scope>). Use "shared" for the common keyspace.
        keyspace: Explicit keyspace override (advanced; bypasses scope).
        custom_key: Optional stable key to store under (for later exact recall/update).
        expire_sec: Optional TTL in seconds (in-memory keyspaces only; 0 = no expiry).
        timestamp: Index a `_created_at` for time-range recall. Defaults to
                   MONTYCAT_AUTO_TIMESTAMP (on). Pass False to skip the
                   server-side timestamp parse when this memory will never be
                   recalled by time.
    """
    ks = await _bind(_resolve_keyspace(scope, keyspace))
    value = _stamp(value, timestamp)
    if custom_key is not None:
        return await ks.insert_custom_key_value(custom_key, value)
    if expire_sec:
        return await ks.insert_value(value, expire_sec=expire_sec)
    return await ks.insert_value(value)


@mcp.tool()
async def montycat_recall(
    keyspace: Optional[str] = None,
    scope: Optional[str] = None,
    key: Optional[str] = None,
    custom_key: Optional[str] = None,
    filters: Optional[dict] = None,
    limit: int = 25,
) -> Any:
    """Recall memory by exact key or by field filter (not by meaning).

    Provide `key`/`custom_key` to fetch a single record, or `filters` (a map of
    field -> value) to look up all records matching those fields. For meaning-based
    recall use montycat_semantic_search instead.

    Args:
        keyspace: Memory namespace (defaults to the configured one).
        key: Montycat-generated key to fetch.
        custom_key: Custom key to fetch.
        filters: Field equality filters, e.g. {"user": "alice", "topic": "billing"}.
        limit: Max results for a filter lookup (default 25).
    """
    ks = await _bind(_resolve_keyspace(scope, keyspace))
    if key is not None or custom_key is not None:
        return await ks.get_value(key=key, custom_key=custom_key)
    if filters:
        return await ks.lookup_values_where(limit=limit, key_included=True, **filters)
    raise ValueError("Provide one of: key, custom_key, or filters.")


@mcp.tool()
async def montycat_list_keyspaces() -> Any:
    """List the available memory stores and keyspaces on this Montycat engine."""
    return await _get_engine().get_structure_available()


@mcp.tool()
async def montycat_create_keyspace(
    keyspace: str,
    persistent: bool = True,
    cache: Optional[int] = None,
    compression: bool = False,
) -> Any:
    """Create a new memory namespace (keyspace). Requires superowner credentials.

    Args:
        keyspace: Name of the keyspace to create.
        persistent: True for durable on-disk storage, False for in-memory.
        cache: Optional cache size in MB (persistent only; min/default 10).
        compression: Enable compression (persistent only).
    """
    ks = _keyspace(keyspace, persistent=persistent)
    _ks_type_cache[keyspace] = persistent  # record type so later ops bind correctly
    if persistent:
        return await ks.create_keyspace(cache=cache, compression=compression)
    return await ks.create_keyspace()


@mcp.tool()
async def montycat_forget(
    keyspace: Optional[str] = None,
    scope: Optional[str] = None,
    key: Optional[str] = None,
    custom_key: Optional[str] = None,
) -> Any:
    """Delete a stored record from memory by key or custom key.

    Args:
        keyspace: Memory namespace (defaults to the configured one).
        key: Montycat-generated key to delete.
        custom_key: Custom key to delete.
    """
    if key is None and custom_key is None:
        raise ValueError("Provide one of: key or custom_key.")
    ks = await _bind(_resolve_keyspace(scope, keyspace))
    return await ks.delete_key(key=key, custom_key=custom_key)


@mcp.tool()
async def montycat_update(
    updates: dict,
    keyspace: Optional[str] = None,
    scope: Optional[str] = None,
    key: Optional[str] = None,
    custom_key: Optional[str] = None,
    expire_sec: int = 0,
) -> Any:
    """Revise an existing memory in place (memory is mutable).

    Use this when a stored fact changes — a corrected value, an updated
    preference — instead of storing a duplicate. Only the fields you pass are
    changed. Identify the record by `key` or `custom_key`.

    Args:
        updates: Fields to change, e.g. {"status": "resolved"} or {"name": "Alice"}.
        keyspace: Memory namespace (defaults to the configured one).
        key: Montycat-generated key of the record to update.
        custom_key: Custom key of the record to update.
        expire_sec: Optional new TTL in seconds (in-memory keyspaces only).
    """
    if key is None and custom_key is None:
        raise ValueError("Provide one of: key or custom_key.")
    ks = await _bind(_resolve_keyspace(scope, keyspace))
    if expire_sec:
        return await ks.update_value(key=key, custom_key=custom_key, expire_sec=expire_sec, **updates)
    return await ks.update_value(key=key, custom_key=custom_key, **updates)


@mcp.tool()
async def montycat_list_memories(
    keyspace: Optional[str] = None,
    scope: Optional[str] = None,
    limit: int = 25,
    recent: bool = True,
) -> Any:
    """Browse stored memories — enumerate what is remembered, not search by meaning.

    Returns up to `limit` records with their keys. Use this to review or list
    memory; for meaning-based recall use montycat_semantic_search, and for exact
    lookups use montycat_recall.

    Args:
        keyspace: Memory namespace (defaults to the configured one).
        limit: Max records to return (default 25).
        recent: Bias toward the most recently written records (default True).
                Ordering is approximate (by storage volume), not a strict timestamp sort.
    """
    ks = await _bind(_resolve_keyspace(scope, keyspace))
    keys_res = await ks.get_keys(latest_volume=recent)
    keys = keys_res.get("payload") if isinstance(keys_res, dict) else None
    if not keys:
        return {"status": True, "payload": [], "error": None}
    keys = list(keys)[:limit]
    return await ks.get_bulk(bulk_keys=keys, key_included=True)


@mcp.tool()
async def montycat_remember_bulk(
    values: list,
    keyspace: Optional[str] = None,
    scope: Optional[str] = None,
    expire_sec: int = 0,
    timestamp: Optional[bool] = None,
) -> Any:
    """Store many memories at once; all are embedded and indexed automatically.

    Args:
        values: A list of records (JSON objects) to store.
        keyspace: Memory namespace (defaults to the configured one).
        expire_sec: Optional TTL in seconds applied to all (in-memory keyspaces only).
        timestamp: Index a `_created_at` on each record for time-range recall.
                   Defaults to MONTYCAT_AUTO_TIMESTAMP (on). Pass False for
                   large imports that will never be recalled by time — it skips
                   a server-side timestamp parse per record.
    """
    ks = await _bind(_resolve_keyspace(scope, keyspace))
    values = [_stamp(v, timestamp) if isinstance(v, dict) else v for v in values]
    if expire_sec:
        return await ks.insert_bulk(bulk_values=values, expire_sec=expire_sec)
    return await ks.insert_bulk(bulk_values=values)


def main() -> None:
    """Console entry point — runs the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
