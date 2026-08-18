# Agentic OS Reference Architecture v3.0

## Product definition

Agentic OS is an enterprise AI control and intelligence platform that securely coordinates humans, AI agents, enterprise knowledge, workflows, tools and business systems through policy-governed autonomy, human oversight, end-to-end observability and evidence-based assurance.

## Plane model

### 1. Experience Plane
Executive, Operator, Analyst, Builder, Auditor and Administrator experiences. Includes Command Center, Work Queue, Ask OS, Approvals, Incidents, Reports and G-Brain.

### 2. Intent and Control Plane
Intent Router, Goal Interpreter, Conductor, Planner, Plan Validator, Risk Engine, Policy Decision Point, Approval Engine, Workflow Orchestrator and Scheduler.

### 3. Agent Plane
Governed domain agents: Sales, Finance, Customer, Operations, Marketing, Analytics, Knowledge, Engineering and Communications. Each agent has identity, contract, owner, risk class, permissions, models, skills and SLOs.

### 4. Skill Plane
Reusable capabilities such as analyse, forecast, classify, draft, reconcile, extract, optimise and calculate. Skills are not independently privileged agents.

### 5. Tool Plane
All external actions pass through the Tool Security Gateway. MCP, APIs, databases, browsers, CRM, ERP, payments, files and calendar connectors are centrally governed.

### 6. Knowledge Plane
Ingestion, malware scanning, OCR, parsing, PII classification, ACL inheritance, chunking, metadata, vector indexing, knowledge graph extraction, hybrid retrieval, reranking and citation verification.

### 7. Data Plane
PostgreSQL, pgvector, object storage, cache, event store, analytics warehouse and append-only audit ledger.

### 8. Model Control Plane
Model Gateway, Model Router, Model Registry, Prompt Registry, Context Firewall, safety filters, cost routing and local/private/cloud execution policy.

### 9. Assurance Plane
Evidence Engine, AI evaluations, RAG evaluations, security testing, agent red teaming, policy validation, regression testing and release certification.

### 10. Security Plane
SSO, MFA, RBAC, ABAC, workload identity, zero trust, KMS, secrets, DLP, PII protection, malware controls and network policy.

### 11. Observability Plane
OpenTelemetry traces, logs, metrics, model usage, retrieval quality, policy decisions, security events, cost, agent behavior, human overrides and business outcomes.

### 12. Platform Plane
Containers, orchestration, queues, event bus, CI/CD, infrastructure as code, backups, disaster recovery, feature flags and runtime isolation.

## Controlled execution path

```text
User -> Intent Router -> Conductor -> Planner -> Plan Validator
     -> Policy Engine -> Workflow Engine -> Agent -> Tool Gateway
     -> External System -> Verification -> Evidence -> Result
```

The Conductor can reason and plan. It cannot directly execute privileged tools.

## Autonomy levels

| Level | Capability | Default governance |
|---|---|---|
| A0 | Observe | Automatic |
| A1 | Analyse and recommend | Automatic |
| A2 | Draft | Human sends/publishes |
| A3 | Reversible execution | Policy controlled |
| A4 | Consequential execution | Human approval required |

## Domain-agent model

The platform intentionally prefers a small number of governed domain agents plus reusable skills over dozens of loosely controlled autonomous agents.
