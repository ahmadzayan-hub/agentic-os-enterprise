# Architecture Constitution

These rules are non-negotiable architectural invariants for Agentic OS Enterprise.

1. No agent directly accesses production systems.
2. No tool executes without authentication, authorization, policy evaluation and audit.
3. No consequential action bypasses risk classification.
4. A4 actions require the configured human authorization policy.
5. No untrusted content is promoted to trusted instruction.
6. No RAG result bypasses tenant and ACL filtering.
7. No agent receives unrestricted credentials.
8. No model or prompt enters production without evaluation and version control.
9. No agent-to-agent communication bypasses the governed message bus.
10. No production feature is complete without executable evidence.
11. No maturity score may be manually overridden.
12. A failed critical control blocks production certification.
13. Material decisions require provenance.
14. External actions must trace to human, agent, policy, workflow and tool identities.
15. Every autonomous capability requires a scoped kill switch.
16. Deterministic computation is preferred where probabilistic AI is unnecessary.
17. The Conductor may plan but may not bypass the workflow and tool gateways.
18. Secrets never enter prompts, logs or model context.
19. Every tenant boundary is enforced in data, retrieval and authorization layers.
20. Rollback and safe degradation are first-class requirements.
