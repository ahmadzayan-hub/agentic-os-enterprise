# Security Operations

Companion to `SECURITY_ARCHITECTURE.md`. That document describes the design;
this one describes what a security team does with it.

## The one rule everything else serves

No agent reaches a production system directly. Every request passes

```
identity → authorization → risk → policy → approval (when required)
        → execution gateway → verification → audit → evidence
```

There is no second path, no debug bypass and no privileged caller that skips
the chain. If you find one, that is a P1.

## Daily

**Watch the ledger, not the logs.** `audit_events` is append-only and
hash-chained. Triggers refuse UPDATE, DELETE and TRUNCATE for every role
including the table owner. Verify a tenant's chain at any time:

```
GET /api/v1/audit/verify?tenant_id=...
```

or `AuditLedger(session).verify_chain(tenant_id)`. A break reports the exact
sequence number where the recomputed hash stopped matching. Treat any break as
an integrity incident: the ledger cannot be edited through the application, so
a break means direct database access or a restore from a tampered copy.

**Watch denials, not just failures.** Policy denials, authorization denials,
risk blocks and contract violations are all recorded with outcome `DENIED`.
A rise in denials for one principal or one agent is the signal that matters —
a successful attack looks like a series of near-misses first.

**Watch the tool gateway.** Every tool call records the decision that permitted
it. A call that reached execution without a matching policy decision is a
control failure, not a logging gap.

## Incident response

**Stop the bleeding without a deploy.** Kill switches take effect on the next
gateway decision:

| Scope | Effect |
|---|---|
| `READ_ONLY` | The platform still answers; every write is refused |
| `TENANT` | That tenant stops entirely |
| `AGENT` | One agent stops; others continue |
| `TOOL` | One tool is refused everywhere |

Engage the narrowest switch that contains the incident. `READ_ONLY` is usually
the right first move for a suspected agent compromise: it preserves the ability
to investigate while removing the ability to act.

**Revoke sessions.** Sessions are rows. Revoking one takes effect on the next
request; there is no cached token to wait out. Refresh tokens are stored as
hashes and a revoked session cannot be refreshed.

**Preserve evidence.** Do not delete. The ledger is append-only by design, and
a legal hold blocks any erasure request that would touch held data. Raise the
hold *before* anyone asks for a deletion, not after.

## Known attack surfaces and how they are held

**Prompt injection.** The context firewall classifies every piece of context
into one of eight trust tiers. Only `SYSTEM_TRUSTED` and `POLICY_TRUSTED`
content may instruct. Retrieved documents, tool output and user-supplied text
are data — they can inform an answer and cannot change what the agent is
allowed to do. Autonomy and tool scope come from the contract, never from
context.

**Confused deputy through retrieval.** Authorization happens *inside* the
retrieval SQL: the ACL predicate and the clearance comparison are part of the
query, so unauthorized content is never fetched. A user cannot discover the
existence of a document they cannot read.

**Tool abuse.** The gateway runs fourteen checks before any side effect,
including agent contract membership, tool scope, autonomy floor, classification
ceiling, kill switches, budgets, rate limits and approval state. Tool
parameters are validated against a JSON Schema; expression evaluation uses an
AST allowlist, not `eval`.

**MCP.** Agents cannot connect to arbitrary MCP servers. A server must be
registered and approved, and its approval is pinned to a schema hash — if the
server's tool schema changes, the approval is automatically revoked and calls
fail until a human re-approves. A database CHECK constraint
(`mcp_no_untrusted_token_forwarding`) makes unrestricted token forwarding
unrepresentable rather than merely discouraged.

**Cross-tenant access.** FORCE row level security on every tenant table with no
bypass predicate. The single BYPASSRLS role is NOLOGIN and reachable only
through `SET ROLE` inside SECURITY DEFINER functions used for provisioning and
pre-authentication lookup. An unbound session sees nothing at all.

**Secret exfiltration through a model.** Secrets are resolved by reference
inside the tool gateway at call time. No secret is placed in a prompt, and
payloads are redacted before they reach the ledger. `.env.example` ships every
credential key blank and a test enforces it; gitleaks runs in CI.

## Reviewing a change

Ask four questions of any pull request that touches the platform:

1. Does it introduce a path to a side effect that does not pass the gateway?
2. Does it add a query that filters by tenant in Python rather than relying on
   RLS, or that retrieves before authorising?
3. Does it place caller-controlled data anywhere an instruction is honoured?
4. Does it claim a control is satisfied without a test that proves it?

Any yes is a blocker.

## What security should not rely on yet

From `docs/assurance/FINAL_GAP_AUDIT.md`, the items that concern this function
directly:

* **No independent assessment has been performed.** The red-team suite is
  written by the same author as the controls it probes. Treat every security
  claim in this repository as self-reported until a second party has tested it.
* **Rate limiting is now shared** across replicas through Redis, but it
  degrades to per-replica limiting if Redis is unreachable — deliberately, so a
  cache outage cannot become an API outage. The log records the transition;
  treat a sustained "shared counter unavailable" warning as a control
  degradation, not just a cache alert.
* **The `vault` and cloud KMS backends have never run against the real
  service.** Production configuration selects exactly those paths.
* **No federation.** Local password plus TOTP only; no OIDC, SAML, SCIM or
  WebAuthn.
* **The DR maintenance identity is effectively a superuser**, because
  `CREATE EXTENSION vector` requires one. It is isolated to a single CronJob
  and a single secret, and that isolation is asserted by a test, but it is the
  largest standing privilege in the design and belongs in the risk register.
