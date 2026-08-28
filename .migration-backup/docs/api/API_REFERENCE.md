# API Reference

Generated from the application's own OpenAPI document by
`scripts/generate_api_reference.py`. Do not edit by hand.

**54 endpoints** under `/api/v1`, plus `/health` and `/ready`.

Authentication is a session cookie issued by `POST /api/v1/auth/login`;
the cookie is httpOnly and the console exchanges it server-side, so the
browser never holds a bearer token. Every endpoint below is authorised
independently of the console: the permission column is what the route
itself requires, and the request is refused without it whatever the
caller's navigation shows.

## auth

| Method | Path | Permission | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — | Login |
| POST | `/api/v1/auth/logout` | — | Logout |
| GET | `/api/v1/auth/me` | — | Me |

## catalog

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/v1/agents` | `agents:read` | List Agents |
| GET | `/api/v1/agents/{agent_key}` | `agents:read` | Agent Detail |
| GET | `/api/v1/connectors` | `connectors:read` | List Connectors |
| GET | `/api/v1/mcp` | `mcp:read` | List Mcp Servers |
| POST | `/api/v1/mcp/{server_key}/classify` | `mcp:write` | Classify Mcp Server |
| GET | `/api/v1/models` | `models:read` | List Models |
| GET | `/api/v1/prompts` | `prompts:read` | List Prompts |
| GET | `/api/v1/prompts/{prompt_key}` | `prompts:read` | Prompt Detail |
| GET | `/api/v1/skills` | `skills:read` | List Skills |
| GET | `/api/v1/tools` | `tools:read` | List Tools |
| GET | `/api/v1/tools/calls` | `tools:read` | Tool Calls |

## governance

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/v1/approvals` | `approvals:read` | List Approvals |
| GET | `/api/v1/approvals/{approval_id}` | `approvals:read` | Get Approval |
| POST | `/api/v1/approvals/{approval_id}/decide` | `approvals:decide` | Decide Approval |
| GET | `/api/v1/audit` | `audit:read` | Audit Log |
| GET | `/api/v1/audit/verify` | `audit:verify` | Verify Audit Chain |
| GET | `/api/v1/evidence` | `evidence:read` | Maturity |
| GET | `/api/v1/evidence/certifications` | `evidence:read` | Certifications |
| GET | `/api/v1/evidence/controls` | `evidence:read` | List Controls |
| GET | `/api/v1/policies` | `policies:read` | List Policies |
| GET | `/api/v1/policies/decisions` | `policies:read` | Policy Decisions |
| GET | `/api/v1/privacy` | `privacy:read` | Privacy Register |
| POST | `/api/v1/privacy/requests` | `privacy:write` | Raise Dsar |
| POST | `/api/v1/privacy/requests/{request_id}/process` | `privacy:write` | Process Dsar |
| GET | `/api/v1/risks` | `risks:read` | List Risks |
| GET | `/api/v1/security` | `security:read` | Security Posture |
| POST | `/api/v1/security/kill-switch` | `killswitch:engage` | Set Kill Switch |

## knowledge

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/v1/datasets` | `knowledge:read` | List Datasets |
| GET | `/api/v1/documents` | `knowledge:read` | List Documents |
| POST | `/api/v1/documents` | `knowledge:write` | Upload Document |
| GET | `/api/v1/documents/{document_id}` | `knowledge:read` | Get Document |
| GET | `/api/v1/graph` | `graph:read` | Query Graph |
| GET | `/api/v1/graph/impact` | `graph:read` | Impact |
| POST | `/api/v1/knowledge/search` | `knowledge:read` | Search |

## operations

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/v1/analytics` | `analytics:read` | Analytics |
| GET | `/api/v1/command-center` | `analytics:read` | Command Center |
| GET | `/api/v1/costs` | `costs:read` | Costs |
| GET | `/api/v1/incidents` | `incidents:read` | List Incidents |
| GET | `/api/v1/organization` | `org:read` | Organization |
| GET | `/api/v1/outcomes` | `outcomes:read` | Outcomes |
| GET | `/api/v1/resilience` | `incidents:read` | Resilience |
| GET | `/api/v1/workflows` | `workflows:read` | List Workflows |
| GET | `/api/v1/workflows/runs` | `workflows:read` | List Workflow Runs |
| POST | `/api/v1/workflows/runs` | `workflows:execute` | Start Workflow |

## platform

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/v1/capabilities` | — | Capabilities |
| GET | `/health` | — | Health |
| GET | `/ready` | — | Ready |

## runs

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/v1/runs` | `runs:read` | List Runs |
| POST | `/api/v1/runs` | `runs:create` | Submit |
| GET | `/api/v1/runs/{run_id}` | `runs:read` | Run Detail |
| POST | `/api/v1/runs/{run_id}/cancel` | `runs:cancel` | Cancel Run |

