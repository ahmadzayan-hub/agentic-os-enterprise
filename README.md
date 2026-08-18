# Agentic OS Enterprise

Enterprise AI control and intelligence platform that coordinates humans, governed AI agents, enterprise knowledge, workflows, tools and business systems through policy-bound autonomy, human oversight, end-to-end observability and evidence-based assurance.

## Reference Architecture

This repository implements the **Agentic OS Reference Architecture v3.0**. The design target is **100/100 reference architecture maturity**. Production certification is intentionally separate and must be derived from executable evidence.

### Core principles

1. No agent directly accesses production systems.
2. Every tool call is identity-aware, policy-enforced, observable and auditable.
3. Consequential actions are risk-classified and human-governed.
4. Untrusted content never becomes trusted instruction.
5. RAG retrieval is tenant- and ACL-aware before ranking.
6. Models, prompts, agents, policies and workflows are versioned controlled assets.
7. Maturity scores are calculated from evidence, never manually asserted.
8. Critical control failures block production certification.

## Architecture planes

1. Experience
2. Intent and Control
3. Agent
4. Skill
5. Tool
6. Knowledge
7. Data
8. Model Control
9. Assurance
10. Security
11. Observability
12. Platform

See [`docs/architecture/REFERENCE_ARCHITECTURE.md`](docs/architecture/REFERENCE_ARCHITECTURE.md).

## Repository layout

```text
apps/web                  Operator and executive UX shell
services/control-plane    Intent, policy, workflow and approval control plane
services/evidence-engine  Evidence ingestion and maturity calculation
packages/contracts        Machine-readable agent contracts
packages/policies         Policy-as-code examples
packages/shared           Shared schemas and types
docs                      Architecture, governance, security and operations
infra                     Container and deployment scaffolding
tests/evals               AI, RAG, policy and agent evaluation fixtures
.github/workflows          CI assurance gates
```

## Quick start

```bash
cp .env.example .env
docker compose -f infra/docker/docker-compose.yml up --build
```

Then open the web shell at `http://localhost:3000` and control-plane health at `http://localhost:8000/health`.

## Maturity status

- Reference architecture design target: **100/100**
- Implementation maturity: **scaffold / not certified**
- Production status: **NOT CERTIFIED**

This distinction is deliberate. A production claim requires verified controls and current evidence.
