# Agentic OS Enterprise

An enterprise AI control and intelligence platform: humans, governed agents,
enterprise knowledge, workflows, tools and business systems coordinated through
policy-bound autonomy, human oversight, end-to-end observability and
evidence-based assurance.

This is executable. `docker compose up --build` gives you a working platform —
a real database with row level security, a governed execution path, a durable
workflow engine, permission-aware retrieval, a hash-chained audit ledger and an
operator console rendering live data. Nothing in the console is a mockup.

**Current evidence-derived maturity: 92.79 / 100 as computed by CI. Not certified.** See
[`docs/assurance/MATURITY_REPORT.md`](docs/assurance/MATURITY_REPORT.md) for
what the missing five points are and
[`docs/assurance/FINAL_GAP_AUDIT.md`](docs/assurance/FINAL_GAP_AUDIT.md) for
everything this build has not proved.

## Run it

```bash
docker compose up --build
```

Console at http://localhost:3000, API at http://localhost:8000, OpenAPI at
http://localhost:8000/api/v1/docs. The `migrate` service prints the demo
credentials. Development placeholders only — nothing in that file belongs in a
deployed environment.

Without Compose, see
[`docs/developer/DEVELOPER_GUIDE.md`](docs/developer/DEVELOPER_GUIDE.md).

## The rule everything serves

No agent reaches a production system directly. Every request passes

```
identity → authorization → risk → policy → approval (when required)
        → execution gateway → verification → audit → evidence
```

There is no second path. The conductor plans and dispatches; it never executes
a tool itself (Architecture Constitution rule 17). Autonomy runs A0–A4, and A4
— consequential or irreversible — always requires a named human.

### Principles, and where each is enforced

| Principle | Enforced by |
|---|---|
| No agent directly accesses production systems | Tool security gateway, 14 checks before any side effect |
| Every tool call is identity-aware, policy-enforced, observable, auditable | Gateway + hash-chained `audit_events` |
| Consequential actions are risk-classified and human-governed | Risk engine, approval engine, autonomy ceiling in the agent contract |
| Untrusted content never becomes trusted instruction | Context firewall, 8 trust tiers; only two may instruct |
| Retrieval is tenant- and ACL-aware *before* ranking | ACL predicate and clearance comparison inside the retrieval SQL |
| Models, prompts, agents, policies, workflows are versioned assets | Declarative registries, validated in CI, versioned in the database |
| Maturity is calculated from evidence, never asserted | `agentic-evidence collect` derives every status from a JUnit report |
| Critical control failure blocks certification | Hard gate in the evidence engine, whatever the score |

## What is here

```text
packages/agentic_os/      the platform: identity, control, runtime, AI, tools,
                          knowledge, privacy, resilience, assurance,
                          observability, outcomes, worker, API
apps/web/                 Next.js 15 operator console — 30 surfaces
database/                 cluster bootstrap and 12 forward-only migrations
packages/contracts/       10 agent contracts, schema-validated
skills/ models/ tools/    declarative registries — the configuration surface
policies/ prompts/
evaluations/              the assurance control catalogue (59 controls)
tests/                    322 tests across 14 suites
infrastructure/           docker, kubernetes, terraform
services/                 thin deployables over the platform package
docs/                     architecture, governance, security, operations,
                          developer, administration, API, assurance
```

| | |
|---|---|
| API endpoints | 54 |
| Console surfaces | 30 |
| Tests | 322, all passing |
| Controls | 58, of which 54 verified |
| Accessibility | 23 surfaces × 2 colour schemes, 0 axe violations |
| Tools | 16 declared, 9 implemented, 7 marked NOT_IMPLEMENTED and refused |

## Documentation

| Audience | Document |
|---|---|
| Building on it | [Developer Guide](docs/developer/DEVELOPER_GUIDE.md) |
| Running it | [Administrator Guide](docs/administration/ADMINISTRATOR_GUIDE.md) · [Runbook](docs/operations/RUNBOOK.md) |
| Securing it | [Security Architecture](docs/security/SECURITY_ARCHITECTURE.md) · [Security Operations](docs/security/SECURITY_OPERATIONS.md) |
| Integrating with it | [API Reference](docs/api/API_REFERENCE.md) |
| Assessing it | [Maturity Report](docs/assurance/MATURITY_REPORT.md) · [Final Gap Audit](docs/assurance/FINAL_GAP_AUDIT.md) |
| Designing with it | [Reference Architecture](docs/architecture/REFERENCE_ARCHITECTURE.md) · [Autonomy Model](docs/governance/AUTONOMY_MODEL.md) · [Maturity Model](docs/governance/MATURITY_MODEL.md) |
| The rules | [Architecture Constitution](ARCHITECTURE_CONSTITUTION.md) |

## Honesty about status

A capability that is not built does not appear as a working control. It appears
on the Capabilities surface marked `NOT_IMPLEMENTED`, and the gateway refuses
it with a typed error.

The same applies to assurance. This platform has **never been deployed to a
cluster, never been independently assessed and never been run in production**.
No claim of conformance with ISO 27001, SOC 2, NIST AI RMF, the EU AI Act or
any other scheme is made anywhere in this repository, because none has been
assessed. The maturity score measures 59 self-authored controls executed in
CI — a reasonable engineering instrument and a weak
assurance instrument, which is precisely why the catalogue now contains
controls the platform cannot yet satisfy.
