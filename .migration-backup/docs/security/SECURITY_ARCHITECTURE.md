# Security Architecture

## Identity model

Every request records four identities where applicable:

- Human identity
- Agent identity
- Workflow/workload identity
- Tool/service identity

Authorization evaluates tenant, role, attributes, resource, action, data classification, agent contract, policy and risk.

## Context firewall

Content is labelled as system-trusted, policy-trusted, enterprise-approved, authenticated-user, external, untrusted-upload or model-generated. Lower-trust content may be analysed but may not elevate itself into control instructions.

## Tool gateway

Every tool call performs:

1. Authenticate identities
2. Authorize agent and user
3. Validate tenant
4. Validate requested scope
5. Evaluate policy
6. Evaluate risk
7. Check approval
8. Validate parameters
9. Inject secrets out of band
10. Execute
11. Sanitize output
12. Verify side effect
13. Record immutable audit evidence

## Kill switches

Global AI stop, agent stop, model stop, tool stop, connector stop, workflow stop and tenant stop are required. Read-only mode must preserve observability while blocking side effects.
