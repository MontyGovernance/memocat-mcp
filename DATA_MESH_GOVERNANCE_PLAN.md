# MemoCat MCP Data-Mesh Governance Analysis and Integration Plan

## Executive conclusion

MemoCat remains functionally compatible with Montycat's data-mesh governance
because the engine enforces read, write, provisioning, semantic, snapshot, and
removal authority server-side. MemoCat should not duplicate that authorization
engine.

It should, however, become policy-aware.

The recommended production architecture is:

```text
superowner
    establishes central policy
        |
        v
delegated Montycat owner credential
    runs one MemoCat MCP instance
        |
        v
agent operates only in permitted stores, keyspace types, models, and keyspaces
```

Normal memory MCP instances should use delegated owner credentials rather than
superowner credentials. Superowner credentials should be reserved for a
separate, explicitly enabled governance administration surface.

## Current architecture and its implications

MemoCat creates one shared Montycat `Engine` from environment variables and
uses that credential identity for the lifetime of the MCP process.

The current scope mapping is:

```text
scope="alice" -> keyspace "mem_alice"
scope="shared" -> configured shared keyspace
explicit keyspace -> keyspace used verbatim
```

`scope` is a keyspace naming convention. It is not an authenticated Montycat
identity.

Consequences:

- With delegated owner credentials, Montycat provides real, keyspace-level
  enforcement.
- With superowner credentials, scopes provide logical organization but not a
  security boundary.
- An explicit `keyspace` argument can target any keyspace accessible to the
  configured credential.
- A superowner-backed MCP process therefore gives the connected agent
  effectively unrestricted database authority.
- One MCP process per owner provides a clean and enforceable production model
  for the current stdio architecture.

The engine already filters `get-structure-available` by effective read
authority, checks data commands per keyspace, authorizes subscriptions when
they connect, and recognizes automatic creator rights. The engine must remain
the final source of truth.

## Current MCP operation mapping

| MCP operation | Required engine authority | Current state |
|---|---|---|
| Semantic search, recall, list memories | `read` | Enforced by engine |
| Remember, bulk remember, update, forget record | `write` | Enforced by engine |
| Watch/subscription | `read` or creator data authority | Engine checks on open; MCP leases revalidate and purge on revocation |
| List keyspaces | Effective readable structure | Already filtered by engine |
| Create keyspace | `provision-keyspace` | Delegated owners supported and documented |
| Remove keyspace | `remove-keyspace` or creator authority | Exposed; watch/resource cleanup precedes removal |
| Enable/disable keyspace semantic management | `manage-semantic` | Exposed keyspace-by-keyspace |
| Snapshot management | `manage-snapshots` | Exposed for existing in-memory keyspaces |
| Effective policy view | Authenticated owner/superowner | Exposed without caller-selected owner impersonation |
| Policy explanation/history | Scoped owner/superowner authority | Exposed through owner-safe tools |
| Policy grant/revoke/deny/apply | Superowner | Not exposed; safer for default profile |

## Compatibility and documentation corrections — complete

The following outdated statements have been corrected:

- `MONTYCAT_AUTO_PROVISION` no longer necessarily needs superowner
  credentials.
- `memocat_create_keyspace` no longer necessarily requires superowner
  credentials.
- `memocat_forget` deletes a record and requires write authority; it does not
  require superowner authority.

A delegated owner can provision a keyspace when the superowner grants
`provision-keyspace` for the target store, requested keyspace type, and
semantic-model constraints.

The Python SDK dependency is pinned to the released `montycat>=1.2.2,<2`,
which contains the typed governance API, scoped semantic lifecycle, external
vectors, status/re-embedding, and subscription cleanup behavior used by
MemoCat.

## Security gaps and design risks

### Superowner credentials in ordinary MCP configurations

The current examples favor an administrative credential. This defeats least
privilege and places a highly authoritative secret in desktop-agent
configuration.

The recommended examples should create a delegated owner and configure MemoCat
with that owner's credentials. The owner should receive only the store-wide
provisioning policy and keyspace-level data access needed by that agent.

### Explicit keyspace overrides

The explicit `keyspace` parameter is useful for advanced deployments, but with
superowner credentials it bypasses the intended meaning of `scope`.

The engine still authorizes the operation, so this is not an engine bypass.
It is a deployment and UX risk.

Recommended controls:

- Default to delegated owner credentials.
- Optionally support `MONTYCAT_ALLOWED_KEYSPACES` or an equivalent allow-list
  for defense in depth.
- Consider disabling explicit keyspace overrides in a strict tenant mode.
- Treat `scope` as routing convenience and never as proof of identity.

### Long-lived subscription revocation

Subscription authorization is checked when the subscription connection opens.
If a superowner later revokes read authority, an already-open subscription may
continue receiving changes until it disconnects.

MemoCat now closes this gap with short authorization leases. Active watches are
periodically revalidated against the engine's filtered structure; access loss
closes the subscription, purges buffered changes, releases resource ownership,
and wakes waiters with an explicit error. Engine-side invalidation remains the
preferred eventual defense for every client, but it is no longer an open MCP
release blocker.

Implemented and future remedies:

1. Engine-side policy changes terminate affected active subscriptions.
2. MemoCat periodically reauthorizes active watches and closes unauthorized
   ones. **Implemented.**
3. Short authorization leases bound the time between policy revocation and
   watch shutdown. **Implemented.**

Engine-side invalidation is preferred because it protects every client, not
only MCP.

MemoCat's buffered watch state must also be discarded when access is revoked;
otherwise a later tool call could replay changes received before the watch was
closed.

### Future multi-session HTTP transport

The current stdio model naturally maps one MCP process to one credential
identity. A future multi-user HTTP transport cannot safely reuse the global
`_engine`.

It will require:

```text
MCP authenticated session -> per-session Montycat Engine and credentials
```

The existing per-session resource notification ownership is useful groundwork,
but resource notification ownership and database authentication are different
concerns.

### Agent-level audit attribution

Montycat can audit the configured owner identity. If several agents share the
same owner credential, the engine cannot distinguish those agents.

For strong audit attribution, use one Montycat owner per agent or service.
Future protocol work could carry a trusted MCP actor/session identifier into
the server audit context, but it must not be accepted as an untrusted,
caller-selected string.

## Recommended owner-facing MCP tools

These tools are safe for the ordinary delegated-owner profile because the
engine remains authoritative:

- `memocat_policy_view`
- `memocat_policy_explain`
- `memocat_policy_history`
- `memocat_create_keyspace`
- `memocat_remove_keyspace`
- `memocat_enable_semantic`
- `memocat_disable_semantic`
- `memocat_start_snapshots`
- `memocat_stop_snapshots`
- `memocat_clean_snapshots`

### Effective policy view

`memocat_policy_view` should return the simplified effective view available to
the configured owner:

- explicit grants and denials;
- readable/writable keyspaces;
- owned keyspaces;
- automatic creator capabilities;
- allowed keyspace types;
- allowed semantic models;
- active restrictions;
- policy health.

This gives the agent useful context without policy mutation authority.

### Policy explanation

`memocat_policy_explain` should explain one proposed action before execution:

- whether it is allowed;
- the policy source;
- applicable constraints;
- whether an explicit denial overrides creator authority;
- whether the keyspace type or semantic model is outside the allow-list.

This lets an agent adapt instead of repeatedly attempting unauthorized actions.

### Keyspace removal

`memocat_remove_keyspace` should expose the creator-removal lifecycle already
supported by the engine.

Before calling the SDK removal method, MemoCat must:

1. stop the keyspace's active watch;
2. release MCP resource-subscription ownership;
3. perform the engine removal;
4. invalidate `_ks_type_cache`;
5. invalidate persistent and in-memory entries in `_keyspaces`.

Caches should be invalidated only when appropriate, and error responses should
remain visible to the agent. An explicit superowner denial must block removal
even for the original creator.

### Keyspace-scoped semantic management

`memocat_enable_semantic` and `memocat_disable_semantic` must require explicit
store/keyspace scope and use the Python SDK's keyspace-scoped semantic methods.

Owners must not receive DB-wide semantic controls. Model selection must be
constrained by policy.

### Snapshot management

Snapshot operations should be keyspace-scoped. The response should distinguish
authorization failure from environmental configuration such as:

```text
Snapshot rate is not set
```

That message indicates missing snapshot scheduling configuration, not a
governance denial.

## Provisioning API improvement

The current MCP tool uses:

```python
persistent: bool
```

An LLM-facing schema is clearer with explicit domain values:

```text
storage: "persistent" | "inmemory"
semantic_model: "minilm" | "bge-small" | "bge-base" | "e5-small"
semantic: true | false
```

Keep `persistent` temporarily for backward compatibility, but prefer `storage`
in the documented API.

If the semantic model is omitted, the engine can choose the first model allowed
by the provisioning policy. The MCP response should report the effective model
selected by the server.

The tool should not duplicate policy evaluation as an authorization decision.
It may call policy explanation for better UX, but it must still submit the
operation and trust the engine's final decision.

## Administrative governance profile

Policy mutations should not be exposed in the normal memory profile. An LLM
with superowner credentials and grant/apply tools could broaden its own
authority or another principal's authority.

If policy administration through MCP is required, expose a separate,
explicitly enabled server profile, for example:

```text
memocat-governance
```

with configuration such as:

```dotenv
MEMOCAT_GOVERNANCE_ADMIN=true
```

Potential administrative tools:

- policy validate;
- policy plan;
- policy export;
- preview grant/revoke;
- grant/revoke;
- deny/remove-denial;
- policy apply.

Recommended safeguards:

- disabled by default;
- distinct MCP server registration and credentials;
- superowner identity required by the engine;
- preview available before mutation;
- mutation responses clearly identify affected owner, capability, and scope;
- clients should require user approval for policy mutations;
- no automatic policy mutation initiated by ordinary memory workflows.

## Deployment modes

### Delegated owner mode

This should be the default production recommendation.

- One MCP process uses one owner credential.
- `scope` maps friendly names to keyspaces.
- Explicit keyspace overrides still receive engine authorization.
- Structure listing reveals only readable keyspaces.
- Auto-provisioning uses delegated policy.
- Creator rights provide read, write, semantic, snapshot, and optionally
  removal authority.

### Superowner bootstrap mode

Use only for:

- initial owner creation;
- central policy administration;
- recovery and transfer operations;
- infrastructure bootstrap.

It should not be the recommended credential mode for ordinary agent memory.

### Shared memory

Shared keyspaces should be modeled with explicit grants:

- read-only organizational knowledge;
- team read/write memory;
- private per-agent memory;
- temporary in-memory working spaces.

This creates server-enforced shared-memory boundaries instead of relying only
on naming conventions.

## Advantages enabled by governance

### Least-privilege autonomous provisioning

An agent can create its own memory keyspaces without receiving superowner
credentials.

Example:

```text
Owner: research-agent
Store: memories
Provisioning types: persistent, inmemory
Allowed models: bge-small
```

The agent may create its own memory, but cannot create stores, use unapproved
models, access another owner's keyspace, change global semantic settings, or
remove another owner's keyspace.

### Controlled semantic-model usage

The superowner can restrict which compiled models an agent may use. This
prevents unexpected model downloads, vector-dimension changes, incompatible
indexes, and uncontrolled resource consumption.

### Revocable autonomy

The superowner can independently suspend:

- keyspace provisioning;
- creator removal;
- semantic management;
- snapshot management.

Read/write authority can remain intact where appropriate.

### Better agent decision-making

Policy view and explain tools let an agent reason about alternatives:

```text
Persistent provisioning is denied, but in-memory provisioning is allowed.
This shared keyspace is readable but not writable.
Only bge-small is allowed for this store.
Keyspace removal is blocked by an explicit denial.
```

This is more useful and safer than repeatedly returning a generic credentials
error.

### Ownership lifecycle

When an owner is retired, owned keyspaces transfer to the superowner. MemoCat
can expose the resulting ownership state through effective policy views and
avoid orphaned agent memory.

### Auditable delegation

Governance history can establish:

- who delegated authority;
- which owner created a keyspace;
- which storage type and model constraints applied;
- when removal or semantic authority was denied;
- when ownership transferred.

## Live E2E test matrix

**Status: complete.** Scenarios 1–14 and 17–22 are covered by
`tests/test_live_governance_matrix.py`; scenarios 15–16 by the live revocation
test in `tests/test_live_governance.py`; and scenarios 23–24 by the live
removal/deadlock test plus lifecycle cache tests.

Governance support should not be declared complete until the following tests
run against a live semantic server.

1. Delegated owner auto-provisions a persistent keyspace.
2. Delegated owner auto-provisions an in-memory keyspace.
3. Disallowed keyspace type is rejected.
4. Allowed semantic model succeeds.
5. Disallowed semantic model is rejected.
6. Creator automatically receives read/write authority.
7. Creator can remove its keyspace.
8. Explicit removal denial blocks creator removal.
9. Explicit semantic denial blocks scoped semantic management.
10. Owner cannot read another owner's keyspace.
11. Owner cannot write another owner's keyspace.
12. `memocat_list_keyspaces` excludes inaccessible keyspaces.
13. MCP resource reads reject inaccessible resource URIs.
14. Resource subscriptions reject inaccessible keyspaces.
15. Revocation terminates or reauthorizes an existing watch.
16. Buffered watch data is unavailable after revocation.
17. Effective policy view includes automatic creator capabilities.
18. Owner policy history is restricted to the owner's scope.
19. Owner retirement transfers owned keyspaces to the superowner.
20. Legacy credentials and existing production users remain unaffected.
21. Superowner mode retains all current behavior.
22. Auto-provisioning returns a useful policy explanation when denied.
23. Keyspace removal releases watches and does not deadlock.
24. Keyspace removal invalidates all MCP keyspace caches.

## Recommended implementation phases

### Phase 1: compatibility and deployment

1. ~~Update README authorization language.~~ ✅
2. ~~Document delegated-owner deployment as the production isolation model.~~ ✅
3. ~~Correct `memocat_forget` documentation.~~ ✅
4. ~~Pin MemoCat to a released Python SDK version containing governance
   support (`montycat>=1.2.2,<2`).~~ ✅
5. ~~Add delegated-owner live fixtures and acceptance coverage.~~ ✅

### Phase 2: owner policy UX

1. ~~Add `memocat_policy_view`.~~ ✅
2. ~~Add `memocat_policy_explain`.~~ ✅
3. ~~Add owner-scoped `memocat_policy_history`.~~ ✅
4. ~~Return actionable policy explanations for auto-provision failures.~~ ✅

### Phase 3: delegated lifecycle

1. ~~Improve the create-keyspace schema.~~ ✅
2. ~~Add `memocat_remove_keyspace`.~~ ✅
3. ~~Add keyspace-scoped semantic management.~~ ✅
4. ~~Add keyspace-scoped snapshot management.~~ ✅
5. ~~Add cache and watch cleanup tests.~~ ✅

### Phase 4: live authorization

1. ~~Implement engine-side subscription invalidation on revocation, or the
   temporary MCP lease/reauthorization fallback.~~ ✅ MCP lease fallback
2. ~~Ensure buffered changes are purged after access loss.~~ ✅
3. ~~Add revoke-while-watching E2E coverage.~~ ✅

### Phase 5: optional governance administration — SKIPPED FOR NOW

Intentionally deferred. The ordinary memory profile remains delegated-owner
focused and exposes no superowner policy mutations.

1. ~~Add a separate opt-in governance MCP profile.~~ Deferred
2. ~~Expose read-only validation, plan, export, and preview first.~~ Deferred
3. ~~Add mutation tools only with explicit configuration and approval semantics.~~ Deferred
4. ~~Test that the ordinary memory profile never exposes superowner mutations.~~ Deferred

### Phase 6: future remote transport

1. Introduce authenticated MCP sessions.
2. Create per-session Montycat engines/credentials.
3. Bind resource subscriptions and watches to the same authenticated session.
4. Prevent cross-session cache, watch, and notification leakage.

## Final design principle

Montycat is the authorization boundary. MemoCat is the policy-aware agent
interface.

MemoCat should provide discovery, explanation, safe lifecycle operations, and
clear errors while leaving every final authorization decision to the engine.
The strongest practical improvement is to remove superowner credentials from
ordinary MCP memory deployments and let centrally defined policy grant agents
only the autonomy they need.
