-- 0014 Index the foreign keys that a cascade has to check.
--
-- PostgreSQL indexes the *referenced* side of a foreign key automatically and
-- the *referencing* side not at all. So every DELETE on a parent row makes the
-- planner prove that no child references it, and without an index that proof is
-- a sequential scan of the child table — once per foreign key, per row deleted.
--
-- Migration 0013 left twenty-two of these unindexed. Six of them point at
-- ``users``, which is precisely the path tenant retirement walks: cascading a
-- user away scans decision_transitions, decisions, kpi_definitions,
-- lessons_learned, actions and decision_outcomes end to end.
--
-- Measured on this hardware with 200,000 transition rows, deleting one user
-- that references nothing — the whole cost being the proof that it references
-- nothing:
--
--     without these indexes    1180 ms cold,  15.2 ms warm
--     with these indexes          7.7 ms cold,  3.5 ms warm
--
-- The cold figure is the one that matters. An offboarding runs against tables
-- nobody has touched recently, and it grows linearly with the child tables
-- while the indexed path stays flat.
--
-- Composite indexes led by ``tenant_id`` already exist for the query paths and
-- are not duplicated here: they serve lookups filtered by tenant, and are no
-- use to a foreign key check, which knows only the referencing column.

-- --- referencing users ------------------------------------------------------
CREATE INDEX decision_transitions_actor_idx ON decision_transitions(actor_user_id);
CREATE INDEX decisions_raised_by_idx        ON decisions(raised_by_user_id);
CREATE INDEX kpi_definitions_owner_idx      ON kpi_definitions(owner_user_id);
CREATE INDEX lessons_learned_recorded_by_idx ON lessons_learned(recorded_by_user_id);
CREATE INDEX actions_executed_by_idx        ON actions(executed_by_user_id);
CREATE INDEX decision_outcomes_verified_by_idx ON decision_outcomes(verified_by_user_id);

-- --- referencing decisions --------------------------------------------------
CREATE INDEX lessons_learned_decision_idx   ON lessons_learned(decision_id);
CREATE INDEX notifications_decision_idx     ON notifications(decision_id);

-- --- referencing decision_options -------------------------------------------
CREATE INDEX actions_option_idx             ON actions(option_id);
CREATE INDEX decision_evidence_option_idx   ON decision_evidence(option_id);
CREATE INDEX recommendations_option_idx     ON recommendations(option_id);

-- --- referencing runs, domains, and the registries --------------------------
CREATE INDEX actions_run_idx                ON actions(run_id);
CREATE INDEX decisions_run_idx              ON decisions(run_id);
CREATE INDEX policy_results_run_idx         ON policy_results(run_id);
CREATE INDEX decisions_approval_idx         ON decisions(approval_id);
CREATE INDEX kpi_definitions_domain_idx     ON kpi_definitions(domain_id);
CREATE INDEX teams_domain_idx               ON teams(domain_id);
CREATE INDEX team_members_team_idx          ON team_members(team_id);
CREATE INDEX decision_outcomes_kpi_idx      ON decision_outcomes(kpi_definition_id);
CREATE INDEX recommendations_model_idx      ON recommendations(model_id);
CREATE INDEX decision_evidence_document_idx ON decision_evidence(document_id);
CREATE INDEX decision_evidence_dataset_idx  ON decision_evidence(dataset_id);
