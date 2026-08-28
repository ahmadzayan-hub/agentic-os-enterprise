# Target Architecture

**Principle:** extend, do not replace. Every element the audit marked load-bearing stays
exactly where it is. The decision intelligence layer is added *above* the existing
control plane and *reuses* its authorization, audit and policy machinery rather than
duplicating it.

---

## 1. The operating loop as a first-class object

```mermaid
stateDiagram-v2
    [*] --> Detected: signal / human raises
    Detected --> Analysing: analysis begins
    Analysing --> RecommendationReady: options + recommendation produced
    Analysing --> Closed: no action warranted
    RecommendationReady --> AwaitingReview: submitted for review
    AwaitingReview --> Analysing: reviewer requests more analysis
    AwaitingReview --> AwaitingApproval: review complete
    AwaitingReview --> Rejected: reviewer rejects
    AwaitingApproval --> Approved: approver approves
    AwaitingApproval --> Rejected: approver rejects
    AwaitingApproval --> AwaitingReview: approver returns for rework
    Approved --> Executing: action dispatched
    Executing --> VerificationPending: action completed
    Executing --> Rejected: execution refused by policy
    VerificationPending --> Verified: outcome measured against target
    VerificationPending --> Closed: outcome unmeasurable, reason recorded
    Verified --> Closed: lesson recorded
    Rejected --> Closed: reason recorded
    Closed --> [*]
```

The eight loop stages map onto the eleven states:

| Loop stage | State(s) | Actor |
|---|---|---|
| DETECT | `Detected` | signal, or a human raising a case |
| ANALYSE | `Analysing` | agent, governed by the existing control plane |
| RECOMMEND | `RecommendationReady` | agent produces options + a recommendation with calculated confidence |
| REVIEW | `AwaitingReview` | Section Lead / Department Manager |
| APPROVE | `AwaitingApproval` → `Approved` / `Rejected` | Approver, MFA step-up |
| EXECUTE | `Executing` | existing tool gateway — never the conductor directly |
| VERIFY | `VerificationPending` → `Verified` | measured against a KPI target |
| LEARN | `Closed` + `lessons_learned` | recorded, feeds future analysis |

**The state column has exactly one writer.** `decision_service.transition()` is the only
code permitted to write `decisions.state`; a database trigger appends to
`decision_transitions` on every change, and that table is append-only in the same manner
as the audit ledger.

---

## 2. System architecture

```mermaid
graph TB
    subgraph EXP["Experience layers"]
        EC["Executive Command<br/>decisions · effectiveness · KPI"]
        DW["Decision Workbench<br/>queue · case · review · approve"]
        PA["Platform Administration<br/>agents · tools · models · policy"]
    end

    subgraph WEB["Next.js console (server components)"]
        API_CLIENT["lib/api.ts<br/>httpOnly cookie, server-side only"]
    end

    subgraph API["FastAPI /v1"]
        DEC["/decisions<br/>lifecycle, options, evidence"]
        KPI["/kpis"]
        NOTIF["/notifications"]
        EXIST["existing 51 endpoints<br/>runs · governance · knowledge · catalog"]
    end

    subgraph SVC["Decision intelligence services"]
        LIFE["Lifecycle engine<br/>sole writer of state"]
        CONF["Confidence calculator<br/>derived or null — never invented"]
        EFF["Effectiveness calculator<br/>verified outcomes only"]
        NOTE["Notification generator"]
    end

    subgraph CORE["Existing control plane — unchanged"]
        AUTHZ["Authorization<br/>RBAC + ABAC + clearance + ACL"]
        POL["Policy engine"]
        APPR["Approval chains + MFA"]
        GATE["Tool security gateway"]
        LEDGER["Hash-chained audit ledger"]
        RET["Permission-aware retrieval"]
    end

    subgraph DATA["PostgreSQL 16 + pgvector — FORCE RLS"]
        NEW[("decisions · decision_options<br/>recommendations · decision_evidence<br/>decision_transitions · decision_outcomes<br/>lessons_learned · actions<br/>kpi_definitions · kpi_values<br/>notifications · domains · team_members<br/>policy_results")]
        OLD[("74 existing tables")]
    end

    EC --> API_CLIENT
    DW --> API_CLIENT
    PA --> API_CLIENT
    API_CLIENT --> DEC & KPI & NOTIF & EXIST
    DEC --> LIFE
    LIFE --> CONF & EFF & NOTE
    DEC --> AUTHZ
    KPI --> AUTHZ
    NOTIF --> AUTHZ
    LIFE --> POL & APPR & LEDGER
    LIFE --> NEW
    EXIST --> OLD
    GATE --> OLD
    RET --> OLD
    AUTHZ -.->|domain scope| NEW
```

**Reuse, explicitly:** the decision layer creates no new authorization mechanism, no new
audit mechanism and no new approval mechanism. `require_permission` guards every new
route exactly as it guards the existing 51. Every state transition writes to the existing
hash-chained ledger. `Approved` is reached through the existing approval chain with its
existing MFA step-up.

---

## 3. Authorization model — including the new domain axis

A caller may read a decision only when **all** of the following hold. They are evaluated
in this order, and the first four are enforced inside the SQL predicate, not after the
fetch:

```mermaid
graph LR
    A["1 · Tenant<br/>RLS, app.tenant_id"] --> B["2 · Domain<br/>membership required"]
    B --> C["3 · Permission<br/>decisions:read"]
    C --> D["4 · Classification<br/>clearance dominance"]
    D --> E["5 · Explicit ACL<br/>where present"]
    E --> F["Row returned"]
```

**Cross-domain access returns 404, not 403.** A 403 discloses that the resource exists.
The directive requires that a user cannot *discover* content they cannot access, so the
domain predicate is part of the `WHERE` clause and a non-member's query returns zero rows
— indistinguishable from the decision not existing.

---

## 4. Confidence — the calculation, stated

Confidence is never a model's self-report and never a constant. It is computed from four
countable inputs, and the computation is stored with the value and rendered to the user:

| Input | Source | Weight |
|---|---|---:|
| Evidence count | `count(decision_evidence)`, capped at 5 | 0.30 |
| Evidence recency | fraction of evidence newer than 90 days | 0.20 |
| Source authority | mean authority weight of linked sources | 0.25 |
| Option separation | normalised score gap between the top two options | 0.25 |

```
confidence = Σ (input_normalised × weight)
```

**If fewer than two options exist, or zero evidence is linked, the result is `null`** and
every surface renders **"Confidence: Not Calculated"**. There is no default, no floor and
no fallback constant. The stored `calculation` JSON records each input's raw and
normalised value so any figure can be reconstructed and challenged.

---

## 5. Decision Effectiveness Rate — the North Star

```
DER = verified_successful_decisions / decisions_reaching_verification
```

Numerator: decisions in state `Verified` whose linked outcome met its target.
Denominator: decisions that reached `VerificationPending` or beyond.

**When the denominator is zero the API returns `null` and the UI renders "Not
Calculated".** It does not return 0%, which would read as total failure, and it does not
return 100%, which would read as total success. Both are lies about an empty set.

---

## 6. Data model additions

```mermaid
erDiagram
    domains ||--o{ decisions : scopes
    domains ||--o{ team_members : contains
    decisions ||--o{ decision_options : "has"
    decisions ||--o{ recommendations : "has"
    decisions ||--o{ decision_evidence : "cites"
    decisions ||--o{ decision_transitions : "records"
    decisions ||--o{ actions : "executes"
    decisions ||--o{ decision_outcomes : "verified by"
    decisions ||--o{ lessons_learned : "teaches"
    decisions ||--o{ notifications : "notifies"
    decisions ||--o{ policy_results : "evaluated by"
    decision_options ||--o| recommendations : "recommended"
    kpi_definitions ||--o{ kpi_values : "measured as"
    kpi_definitions ||--o{ decision_outcomes : "target for"
```

All new tables carry `tenant_id` with `FORCE ROW LEVEL SECURITY` and the same
`app.tenant_id` predicate as the existing 74. `decision_transitions` additionally carries
append-only triggers modelled on the audit ledger's.

---

## 7. Experience layers

| Layer | Route | Primary user | Answers |
|---|---|---|---|
| **Executive Command** | `/` | Executive, Dept Manager | What needs my attention? Are our decisions working? Are we on target? |
| **Decision Workbench** | `/decisions`, `/decisions/[id]` | Engineer, Section Lead, Dept Manager | What is this decision, what are the options, what is the evidence, what do I do? |
| **Platform Administration** | existing `/agents/*`, `/governance/*`, `/security`, `/operations/*` | Platform Admin, Governance, Cyber | Is the platform correct, safe and evidenced? |

The existing 30 routes are **preserved unchanged**. Platform Administration *is* the
existing console; the change is that it stops being the only layer and stops being the
default landing surface for users who are not administrators.

---

## 8. What is explicitly not in this architecture

- No new authentication mechanism.
- No new audit mechanism.
- No client-side state store.
- No microservice split.
- No event bus beyond the existing outbox.
- No AI confidence that is not calculated from stored inputs.
- No integration with an external system this environment cannot reach.
