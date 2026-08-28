# Evidence-Based Maturity Report

- **Score**: 95.59/100
- **Certified**: no
- **Environment**: development
- **Commit**: `unknown`
- **Generated**: 2026-08-28T13:28:56.454683+00:00

Test run: 428 tests, 0 failures, 0 errors, 0 skipped.

## Domain scores

| Domain | Score | Weight | Verified | Failed | Not evidenced |
|---|---:|---:|---:|---:|---:|
| agent_architecture | 100.0 | 10 | 4 | 0 | 0 |
| ai_governance | 100.0 | 7 | 4 | 0 | 0 |
| business_architecture | 100.0 | 7 | 3 | 0 | 0 |
| business_value | 100.0 | 2 | 1 | 0 | 0 |
| data_architecture | 100.0 | 7 | 3 | 0 | 0 |
| decision_intelligence | 100.0 | 23 | 10 | 0 | 0 |
| deployment | 50.0 | 4 | 2 | 0 | 1 |
| devsecops | 100.0 | 4 | 2 | 0 | 0 |
| dr_resilience | 100.0 | 2 | 1 | 0 | 0 |
| enterprise_architecture | 100.0 | 8 | 3 | 0 | 0 |
| evaluation_assurance | 100.0 | 6 | 4 | 0 | 0 |
| independent_assurance | 0.0 | 3 | 0 | 0 | 2 |
| observability | 100.0 | 5 | 3 | 0 | 0 |
| performance | 66.7 | 3 | 2 | 0 | 1 |
| privacy | 100.0 | 5 | 3 | 0 | 0 |
| rag_knowledge | 100.0 | 7 | 3 | 0 | 0 |
| reliability | 100.0 | 6 | 3 | 0 | 0 |
| security | 100.0 | 10 | 7 | 0 | 0 |
| ux_accessibility | 100.0 | 9 | 4 | 0 | 0 |
| workflow_orchestration | 100.0 | 8 | 4 | 0 | 0 |

## Controls

| Control | Domain | Weight | Critical | Status | Test |
|---|---|---:|:---:|---|---|
| AGT-001 | agent_architecture | 3 | yes | VERIFIED | `tests/security/test_tool_gateway.py::test_tool_outside_the_agent_contract_is_denied` |
| AGT-002 | agent_architecture | 3 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_agent_cannot_exceed_its_autonomy_ceiling` |
| AGT-003 | agent_architecture | 2 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_no_contract_grants_autonomous_a4` |
| AGT-004 | agent_architecture | 2 |  | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_agent_budget_stops_at_the_limit` |
| AIG-001 | ai_governance | 2 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_financial_actions_are_always_critical_and_a4` |
| AIG-002 | ai_governance | 2 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_the_same_person_cannot_satisfy_a_dual_approval` |
| AIG-003 | ai_governance | 1 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_an_approval_for_one_action_does_not_authorise_another` |
| AIG-004 | ai_governance | 2 |  | VERIFIED | `tests/integration/test_prompt_registry.py::test_tampered_prompt_body_is_rejected` |
| BUS-001 | business_architecture | 2 |  | VERIFIED | `tests/agents/test_contract_validation.py::test_all_declared_domain_agents_exist` |
| BUS-002 | business_architecture | 2 |  | VERIFIED | `tests/agents/test_contract_validation.py::test_registries_are_internally_consistent` |
| BUS-003 | business_architecture | 3 |  | VERIFIED | `tests/integration/test_business_outcomes.py::test_measured_outcome_requires_evidence` |
| BVL-001 | business_value | 2 |  | VERIFIED | `tests/integration/test_business_outcomes.py::test_roi_excludes_estimated_outcomes` |
| DAT-001 | data_architecture | 3 | yes | VERIFIED | `tests/tenant_isolation/test_rls_isolation.py::test_every_tenant_table_has_forced_rls` |
| DAT-002 | data_architecture | 3 | yes | VERIFIED | `tests/tenant_isolation/test_rls_isolation.py::test_unbound_session_sees_nothing` |
| DAT-003 | data_architecture | 1 | yes | VERIFIED | `tests/tenant_isolation/test_rls_isolation.py::test_application_role_cannot_bypass_rls` |
| DEC-001 | decision_intelligence | 2 |  | VERIFIED | `tests/decisions/test_schema_guarantees.py::test_every_new_table_forces_row_level_security` |
| DEC-002 | decision_intelligence | 3 | yes | VERIFIED | `tests/decisions/test_lifecycle.py::test_every_ordered_pair_of_states_is_legal_or_illegal_as_declared` |
| DEC-003 | decision_intelligence | 2 |  | VERIFIED | `tests/decisions/test_lifecycle.py::test_nothing_outside_the_lifecycle_module_writes_the_state_column` |
| DEC-004 | decision_intelligence | 2 |  | VERIFIED | `tests/decisions/test_schema_guarantees.py::test_the_transition_log_refuses_update_even_for_the_provisioning_role` |
| DEC-005 | decision_intelligence | 3 | yes | VERIFIED | `tests/decisions/test_schema_guarantees.py::test_a_confidence_with_the_default_empty_calculation_is_refused` |
| DEC-006 | decision_intelligence | 3 | yes | VERIFIED | `tests/decisions/test_domain_isolation.py::test_the_database_returns_nothing_rather_than_filtering_afterwards` |
| DEC-007 | decision_intelligence | 2 |  | VERIFIED | `tests/decisions/test_lifecycle.py::test_a_section_lead_can_review_but_cannot_approve` |
| DEC-008 | decision_intelligence | 2 |  | VERIFIED | `tests/decisions/test_lifecycle.py::test_an_agent_cannot_approve_or_verify_on_its_own` |
| DEC-009 | decision_intelligence | 2 |  | VERIFIED | `tests/decisions/test_confidence_and_effectiveness.py::test_the_rate_is_not_calculated_over_an_empty_set` |
| DEC-010 | decision_intelligence | 2 |  | VERIFIED | `tests/decisions/test_decision_api.py::test_every_kpi_carries_its_definition` |
| DEP-001 | deployment | 1 |  | VERIFIED | `tests/unit/test_deployables.py::test_kubernetes_workloads_run_unprivileged_and_carry_no_secrets` |
| DEP-002 | deployment | 1 | yes | VERIFIED | `tests/unit/test_deployables.py::test_only_the_dr_cronjob_mounts_the_maintenance_identity` |
| DEP-003 | deployment | 2 |  | NOT_EVIDENCED | `—` |
| DEV-001 | devsecops | 2 |  | VERIFIED | `tests/database/test_migrations.py::test_modified_migration_is_rejected` |
| DEV-002 | devsecops | 2 |  | VERIFIED | `tests/api/test_repository_hygiene.py::test_ci_pipeline_covers_required_gates` |
| DRP-001 | dr_resilience | 2 |  | VERIFIED | `tests/integration/test_disaster_recovery.py::test_a_restore_is_performed_and_verified_end_to_end` |
| EA-001 | enterprise_architecture | 3 | yes | VERIFIED | `tests/agents/test_contract_validation.py::test_registries_are_internally_consistent` |
| EA-002 | enterprise_architecture | 3 | yes | VERIFIED | `tests/agents/test_contract_validation.py::test_conductor_holds_no_tool_authority` |
| EA-003 | enterprise_architecture | 2 |  | VERIFIED | `tests/security/test_tool_gateway.py::test_unimplemented_tool_is_refused_not_faked` |
| EVL-001 | evaluation_assurance | 2 | yes | VERIFIED | `tests/integration/test_evidence_engine.py::test_maturity_is_derived_only_from_test_results` |
| EVL-002 | evaluation_assurance | 2 | yes | VERIFIED | `tests/integration/test_evidence_engine.py::test_critical_failure_blocks_certification` |
| EVL-003 | evaluation_assurance | 1 |  | VERIFIED | `tests/integration/test_evidence_engine.py::test_expired_evidence_does_not_count` |
| EVL-004 | evaluation_assurance | 1 | yes | VERIFIED | `tests/test_service_gates.py::test_an_absent_service_fails_when_it_is_required` |
| IND-001 | independent_assurance | 2 |  | NOT_EVIDENCED | `—` |
| IND-002 | independent_assurance | 1 |  | NOT_EVIDENCED | `—` |
| OBS-001 | observability | 2 | yes | VERIFIED | `tests/security/test_tool_gateway.py::test_every_call_is_recorded_and_audited` |
| OBS-002 | observability | 2 | yes | VERIFIED | `tests/database/test_audit_ledger.py::test_ledger_rejects_mutation` |
| OBS-003 | observability | 1 |  | VERIFIED | `tests/api/test_api_surface.py::test_run_detail_exposes_governance_record` |
| PRF-001 | performance | 1 |  | VERIFIED | `tests/performance/test_slo_conformance.py::test_every_request_succeeded_at_every_concurrency` |
| PRF-002 | performance | 1 |  | VERIFIED | `tests/performance/test_slo_conformance.py::test_uncontended_latency_has_not_regressed` |
| PRF-003 | performance | 1 |  | NOT_EVIDENCED | `—` |
| PRV-001 | privacy | 2 |  | VERIFIED | `tests/integration/test_ingestion_pipeline.py::test_pii_raises_document_classification` |
| PRV-002 | privacy | 2 |  | VERIFIED | `tests/integration/test_ingestion_pipeline.py::test_redaction_leaves_no_residual_fragments` |
| PRV-003 | privacy | 1 |  | VERIFIED | `tests/integration/test_privacy_dsar.py::test_erasure_is_blocked_by_an_active_legal_hold` |
| RAG-001 | rag_knowledge | 3 | yes | VERIFIED | `tests/rag/test_retrieval_governance.py::test_restricted_content_never_leaks_below_clearance` |
| RAG-002 | rag_knowledge | 2 |  | VERIFIED | `tests/rag/test_retrieval_governance.py::test_fetch_document_denies_unauthorised_and_hides_existence` |
| RAG-003 | rag_knowledge | 2 |  | VERIFIED | `tests/rag/test_retrieval_governance.py::test_citation_verification_rejects_unsupported_claims` |
| REL-001 | reliability | 2 | yes | VERIFIED | `tests/security/test_tool_gateway.py::test_repeated_call_with_the_same_key_does_not_re_execute` |
| REL-002 | reliability | 2 |  | VERIFIED | `tests/workflows/test_workflow_engine.py::test_claim_leases_runs_exclusively` |
| REL-003 | reliability | 2 |  | VERIFIED | `tests/integration/test_event_bus.py::test_outbox_commits_with_the_state_change` |
| SEC-001 | security | 2 | yes | VERIFIED | `tests/security/test_tool_gateway.py::test_unauthenticated_call_is_denied_at_identity` |
| SEC-002 | security | 2 | yes | VERIFIED | `tests/security/test_context_firewall.py::test_only_the_top_two_tiers_may_instruct` |
| SEC-003 | security | 1 |  | VERIFIED | `tests/security/test_context_firewall.py::test_high_confidence_injection_is_blocked_not_rendered` |
| SEC-004 | security | 2 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_secrets_never_survive_audit_redaction` |
| SEC-005 | security | 1 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_forged_token_claims_do_not_grant_access` |
| SEC-006 | security | 1 | yes | VERIFIED | `tests/redteam/test_agentic_red_team.py::test_sandboxed_evaluator_refuses_code_execution` |
| SEC-007 | security | 1 | yes | VERIFIED | `tests/security/test_tool_gateway.py::test_kill_switch_blocks_every_tool` |
| UX-001 | ux_accessibility | 2 |  | VERIFIED | `tests/api/test_api_surface.py::test_openapi_document_is_served` |
| UX-002 | ux_accessibility | 3 |  | VERIFIED | `tests/api/test_api_surface.py::test_run_detail_exposes_governance_record` |
| UX-003 | ux_accessibility | 2 |  | VERIFIED | `tests/accessibility/test_wcag_conformance.py::test_no_serious_or_critical_accessibility_violations` |
| UX-004 | ux_accessibility | 2 |  | VERIFIED | `tests/i18n/test_bidirectional_layout.py::test_no_stylesheet_rule_pins_itself_to_a_physical_side` |
| WKF-001 | workflow_orchestration | 2 |  | VERIFIED | `tests/workflows/test_workflow_engine.py::test_workflow_runs_to_completion` |
| WKF-002 | workflow_orchestration | 2 | yes | VERIFIED | `tests/workflows/test_workflow_engine.py::test_completed_step_is_never_re_executed` |
| WKF-003 | workflow_orchestration | 2 |  | VERIFIED | `tests/workflows/test_workflow_engine.py::test_retryable_failure_retries_then_dead_letters` |
| WKF-004 | workflow_orchestration | 2 |  | VERIFIED | `tests/workflows/test_workflow_engine.py::test_failure_compensates_completed_steps_in_reverse` |
