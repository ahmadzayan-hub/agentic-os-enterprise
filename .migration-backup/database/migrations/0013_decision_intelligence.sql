-- 0013 Decision intelligence.
--
-- The platform could run agents safely and could not represent a decision.
-- Approvals attached to *runs*, and a run is a record of what the machine did:
-- it starts, it ends within minutes, and it is finished. A decision is a record
-- of what the organisation chose, on what evidence, and whether it worked —
-- and whether it worked is knowable weeks later, long after every run involved
-- has finished. Those are different objects with different lifetimes, and
-- conflating them is why nothing here could answer whether a decision was any
-- good.
--
-- This migration adds that object and the five stages of the operating loop
-- that had no storage: DETECT, ANALYSE, RECOMMEND, VERIFY, LEARN.
--
-- Two design commitments are enforced here rather than left to application
-- code, because application code can be bypassed and a constraint cannot:
--
--  * ``decision_transitions`` is append-only under the same triggers as the
--    audit ledger. A decision's history is evidence, and evidence that can be
--    rewritten is not evidence.
--  * ``recommendations.confidence`` may be NULL, and a CHECK constraint forbids
--    a non-NULL confidence without the stored calculation that produced it.
--    An invented confidence percentage is the most damaging thing this product
--    could display — authoritative-looking and unfalsifiable — so the database
--    refuses to hold one.

-- --- 1. the domain boundary -------------------------------------------------
-- The audit found ``agents.domain`` was an unconstrained string, so the
-- requirement that unauthorized cross-domain access be zero had no entity to
-- enforce against. A string column is not a boundary.

CREATE TABLE domains (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  slug            text NOT NULL CHECK (slug ~ '^[a-z][a-z0-9_-]{2,63}$'),
  name            text NOT NULL,
  description     text NOT NULL DEFAULT '',
  classification  data_classification NOT NULL DEFAULT 'INTERNAL',
  status          lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, slug)
);

CREATE TABLE teams (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  domain_id       uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
  slug            text NOT NULL CHECK (slug ~ '^[a-z][a-z0-9_-]{2,63}$'),
  name            text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, slug)
);

-- Membership is the authorization fact. A user with decisions:read but no
-- membership in a domain sees nothing from that domain — and, per the brief,
-- cannot discover that anything is there.
CREATE TABLE team_members (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  domain_id       uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
  team_id         uuid REFERENCES teams(id) ON DELETE SET NULL,
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  membership_role text NOT NULL DEFAULT 'MEMBER'
                    CHECK (membership_role IN ('MEMBER', 'LEAD', 'MANAGER')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, domain_id, user_id)
);
CREATE INDEX team_members_user_idx ON team_members(tenant_id, user_id);

-- --- 2. the decision case ---------------------------------------------------

CREATE TYPE decision_state AS ENUM (
  'DETECTED',
  'ANALYSING',
  'RECOMMENDATION_READY',
  'AWAITING_REVIEW',
  'AWAITING_APPROVAL',
  'APPROVED',
  'REJECTED',
  'EXECUTING',
  'VERIFICATION_PENDING',
  'VERIFIED',
  'CLOSED'
);

CREATE TABLE decisions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  domain_id       uuid NOT NULL REFERENCES domains(id) ON DELETE RESTRICT,
  reference       text NOT NULL,
  title           text NOT NULL CHECK (length(title) BETWEEN 3 AND 300),
  summary         text NOT NULL DEFAULT '',
  -- Why this case exists at all: a signal fired, or a person raised it.
  detected_by     text NOT NULL DEFAULT 'SIGNAL'
                    CHECK (detected_by IN ('SIGNAL', 'HUMAN', 'AGENT', 'SCHEDULE')),
  detection_source text NOT NULL DEFAULT '',
  state           decision_state NOT NULL DEFAULT 'DETECTED',
  classification  data_classification NOT NULL DEFAULT 'INTERNAL',
  risk            risk_class NOT NULL DEFAULT 'MEDIUM',
  -- The person accountable for the decision, distinct from whoever approves it.
  owner_user_id   uuid REFERENCES users(id) ON DELETE SET NULL,
  raised_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  run_id          uuid REFERENCES runs(id) ON DELETE SET NULL,
  approval_id     uuid REFERENCES approvals(id) ON DELETE SET NULL,
  due_at          timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  closed_at       timestamptz,
  UNIQUE (tenant_id, reference)
);
CREATE INDEX decisions_queue_idx ON decisions(tenant_id, state, created_at DESC);
CREATE INDEX decisions_domain_idx ON decisions(tenant_id, domain_id);
CREATE INDEX decisions_owner_idx ON decisions(tenant_id, owner_user_id);

-- --- 3. options and the recommendation --------------------------------------

CREATE TABLE decision_options (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id     uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  label           text NOT NULL,
  description     text NOT NULL DEFAULT '',
  -- Scores are the analyst's or agent's assessment on a stated scale, not a
  -- probability. The column name says score, and the UI must not render it as
  -- a confidence or a likelihood.
  score           numeric(6,4) CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
  estimated_cost  numeric(18,2),
  currency        text NOT NULL DEFAULT 'AED',
  risk            risk_class NOT NULL DEFAULT 'MEDIUM',
  reversible      boolean NOT NULL DEFAULT true,
  -- What happens if we do nothing is an option, and pretending otherwise is
  -- how organisations end up with a single-option "choice".
  is_status_quo   boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decision_options_decision_idx ON decision_options(tenant_id, decision_id);

CREATE TABLE recommendations (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id     uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  option_id       uuid REFERENCES decision_options(id) ON DELETE SET NULL,
  rationale       text NOT NULL DEFAULT '',
  -- A concise reasoning summary. Never hidden chain-of-thought: this is the
  -- explanation shown to the reviewer, and it is the only reasoning stored.
  reasoning_summary text NOT NULL DEFAULT '',
  produced_by     text NOT NULL DEFAULT 'AGENT'
                    CHECK (produced_by IN ('AGENT', 'HUMAN', 'HYBRID')),
  model_id        uuid REFERENCES models(id) ON DELETE SET NULL,
  -- NULL means "Not Calculated" and must render as those words. There is no
  -- default, no floor, and no fallback constant.
  confidence      numeric(5,4) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  confidence_calculation jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  -- The constraint that makes an invented confidence impossible to store: a
  -- figure without its inputs is refused by the database, not by a reviewer.
  -- coalesce, not a bare comparison: with the default '{}' the -> operator
  -- yields SQL NULL, jsonb_typeof(NULL) is NULL, and a CHECK only rejects on
  -- FALSE. Written the obvious way this constraint was inert for exactly the
  -- value it most needed to catch, which a probe caught before it shipped.
  CONSTRAINT recommendation_confidence_is_calculated CHECK (
    confidence IS NULL
    OR (coalesce(jsonb_typeof(confidence_calculation -> 'inputs'), '') = 'array'
        AND jsonb_array_length(confidence_calculation -> 'inputs') > 0)
  )
);
CREATE INDEX recommendations_decision_idx ON recommendations(tenant_id, decision_id);

-- --- 4. evidence ------------------------------------------------------------
-- Evidence is what a confidence figure is computed from, so it carries the
-- three properties that computation needs: how authoritative the source is,
-- when it was observed, and what it points at.

CREATE TABLE decision_evidence (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id     uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  option_id       uuid REFERENCES decision_options(id) ON DELETE CASCADE,
  source_kind     text NOT NULL
                    CHECK (source_kind IN ('DOCUMENT', 'DATASET', 'METRIC', 'RUN',
                                           'INCIDENT', 'AUDIT', 'EXTERNAL', 'HUMAN')),
  source_ref      text NOT NULL DEFAULT '',
  document_id     uuid REFERENCES documents(id) ON DELETE SET NULL,
  dataset_id      uuid REFERENCES datasets(id) ON DELETE SET NULL,
  summary         text NOT NULL DEFAULT '',
  -- 1.00 is a primary authoritative record; 0.25 is hearsay. The weight is
  -- stored so the confidence calculation can be reconstructed and challenged.
  authority_weight numeric(4,3) NOT NULL DEFAULT 0.500
                    CHECK (authority_weight > 0 AND authority_weight <= 1),
  observed_at     timestamptz NOT NULL DEFAULT now(),
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decision_evidence_decision_idx ON decision_evidence(tenant_id, decision_id);

-- --- 5. the transition log --------------------------------------------------
-- Append-only, under the same triggers as the audit ledger. A decision's
-- history is evidence, and evidence that can be rewritten is not evidence.

CREATE TABLE decision_transitions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id     uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  from_state      decision_state,
  to_state        decision_state NOT NULL,
  actor_user_id   uuid REFERENCES users(id) ON DELETE SET NULL,
  actor_kind      text NOT NULL DEFAULT 'HUMAN'
                    CHECK (actor_kind IN ('HUMAN', 'AGENT', 'SYSTEM')),
  reason          text NOT NULL DEFAULT '',
  occurred_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decision_transitions_decision_idx
  ON decision_transitions(tenant_id, decision_id, occurred_at);

CREATE OR REPLACE FUNCTION decision_transitions_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'decision_transitions is append-only (attempted %)', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER decision_transitions_no_update
  BEFORE UPDATE ON decision_transitions
  FOR EACH ROW EXECUTE FUNCTION decision_transitions_append_only();

CREATE TRIGGER decision_transitions_no_delete
  BEFORE DELETE ON decision_transitions
  FOR EACH ROW EXECUTE FUNCTION decision_transitions_append_only();

-- FOR EACH STATEMENT: a TRUNCATE fires no row triggers, which is exactly how
-- 0009 found the audit ledger could have been emptied silently.
CREATE TRIGGER decision_transitions_no_truncate
  BEFORE TRUNCATE ON decision_transitions
  FOR EACH STATEMENT EXECUTE FUNCTION decision_transitions_append_only();

-- --- 6. governed actions ----------------------------------------------------
-- What was actually done, as a business fact with an owner and a reversal
-- path — distinct from the run steps that carried it out.

CREATE TABLE actions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id     uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  option_id       uuid REFERENCES decision_options(id) ON DELETE SET NULL,
  title           text NOT NULL,
  action_kind     text NOT NULL DEFAULT 'MANUAL'
                    CHECK (action_kind IN ('MANUAL', 'AGENT_TOOL', 'WORKFLOW', 'EXTERNAL')),
  status          text NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'REVERSED')),
  reversible      boolean NOT NULL DEFAULT true,
  reversal_plan   text NOT NULL DEFAULT '',
  executed_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  run_id          uuid REFERENCES runs(id) ON DELETE SET NULL,
  result          jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at      timestamptz,
  completed_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX actions_decision_idx ON actions(tenant_id, decision_id);

-- --- 7. KPI framework -------------------------------------------------------
-- The audit found metric_samples had no definition, no target, no owner and no
-- direction, which means any KPI rendered from it was a number without a
-- meaning. A value cannot exist here without its definition.

CREATE TABLE kpi_definitions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  domain_id       uuid REFERENCES domains(id) ON DELETE SET NULL,
  kpi_key         text NOT NULL CHECK (kpi_key ~ '^[a-z][a-z0-9_.]{2,63}$'),
  name            text NOT NULL,
  description     text NOT NULL DEFAULT '',
  -- How the number is produced, in words a reviewer can check.
  formula         text NOT NULL,
  unit            text NOT NULL DEFAULT 'unit',
  -- Whether higher is better decides whether a rise is green or red. Getting
  -- this wrong inverts every dashboard built on it.
  direction       text NOT NULL DEFAULT 'UP_IS_GOOD'
                    CHECK (direction IN ('UP_IS_GOOD', 'DOWN_IS_GOOD')),
  target_value    numeric(20,6),
  warning_value   numeric(20,6),
  owner_user_id   uuid REFERENCES users(id) ON DELETE SET NULL,
  source_system   text NOT NULL DEFAULT 'platform',
  status          lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, kpi_key)
);

CREATE TABLE kpi_values (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kpi_definition_id uuid NOT NULL REFERENCES kpi_definitions(id) ON DELETE CASCADE,
  period_start    timestamptz NOT NULL,
  period_end      timestamptz NOT NULL,
  value           numeric(20,6) NOT NULL,
  sample_count    integer NOT NULL DEFAULT 0,
  -- MEASURED means the platform recorded the inputs. ESTIMATED means somebody
  -- supplied it. They are never mixed in a computation, matching the rule the
  -- outcome engine already applies to ROI.
  basis           text NOT NULL DEFAULT 'MEASURED'
                    CHECK (basis IN ('MEASURED', 'ESTIMATED')),
  computed_from   jsonb NOT NULL DEFAULT '{}'::jsonb,
  computed_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (period_end >= period_start),
  UNIQUE (tenant_id, kpi_definition_id, period_start, period_end)
);
CREATE INDEX kpi_values_definition_idx
  ON kpi_values(tenant_id, kpi_definition_id, period_end DESC);

-- --- 8. outcome verification and learning -----------------------------------
-- The two stages that make the North Star KPI computable. Without these,
-- "did the decision work?" has no answer and Decision Effectiveness Rate has
-- no denominator.

CREATE TABLE decision_outcomes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id     uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  kpi_definition_id uuid REFERENCES kpi_definitions(id) ON DELETE SET NULL,
  target_value    numeric(20,6),
  actual_value    numeric(20,6),
  unit            text NOT NULL DEFAULT 'unit',
  -- The verdict. UNVERIFIABLE is a first-class result: some decisions cannot
  -- be measured, and recording that honestly is better than leaving the case
  -- open forever or quietly counting it as a success.
  verdict         text NOT NULL DEFAULT 'PENDING'
                    CHECK (verdict IN ('PENDING', 'ACHIEVED', 'PARTIAL', 'NOT_ACHIEVED', 'UNVERIFIABLE')),
  verification_method text NOT NULL DEFAULT '',
  verified_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  verified_at     timestamptz,
  notes           text NOT NULL DEFAULT '',
  created_at      timestamptz NOT NULL DEFAULT now(),
  -- A verdict other than PENDING is a claim about reality, so it must name who
  -- checked, when, and how.
  CONSTRAINT outcome_verdict_requires_verification CHECK (
    verdict = 'PENDING'
    OR (verified_at IS NOT NULL AND length(verification_method) > 0)
  )
);
CREATE INDEX decision_outcomes_decision_idx ON decision_outcomes(tenant_id, decision_id);

CREATE TABLE lessons_learned (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  decision_id     uuid NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  domain_id       uuid REFERENCES domains(id) ON DELETE SET NULL,
  lesson          text NOT NULL CHECK (length(lesson) >= 10),
  category        text NOT NULL DEFAULT 'PROCESS'
                    CHECK (category IN ('PROCESS', 'DATA', 'MODEL', 'POLICY', 'EXECUTION', 'ESTIMATION')),
  recorded_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX lessons_learned_domain_idx ON lessons_learned(tenant_id, domain_id);

-- --- 9. notifications -------------------------------------------------------
-- The audit found a pending approval was discovered by opening the console and
-- looking. That is not a workflow, it is a habit.

CREATE TABLE notifications (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  recipient_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  decision_id     uuid REFERENCES decisions(id) ON DELETE CASCADE,
  kind            text NOT NULL
                    CHECK (kind IN ('REVIEW_REQUESTED', 'APPROVAL_REQUESTED', 'DECISION_APPROVED',
                                    'DECISION_REJECTED', 'VERIFICATION_DUE', 'OUTCOME_RECORDED')),
  subject         text NOT NULL,
  body            text NOT NULL DEFAULT '',
  read_at         timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notifications_inbox_idx
  ON notifications(tenant_id, recipient_user_id, read_at, created_at DESC);

-- --- 10. persisted policy results -------------------------------------------
-- Policy decisions were computed and logged but never stored as rows, so
-- "show me every decision this policy affected last quarter" had no answer.

CREATE TABLE policy_results (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  policy_id       uuid REFERENCES policies(id) ON DELETE SET NULL,
  decision_id     uuid REFERENCES decisions(id) ON DELETE CASCADE,
  run_id          uuid REFERENCES runs(id) ON DELETE CASCADE,
  effect          policy_effect NOT NULL,
  matched         boolean NOT NULL DEFAULT true,
  rationale       text NOT NULL DEFAULT '',
  evaluated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX policy_results_decision_idx ON policy_results(tenant_id, decision_id);
CREATE INDEX policy_results_policy_idx ON policy_results(tenant_id, policy_id, evaluated_at DESC);

-- --- 11. isolation and grants -----------------------------------------------
-- Same policy as every other tenant-scoped table. There is deliberately no
-- bypass predicate here either.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'domains', 'teams', 'team_members', 'decisions', 'decision_options',
    'recommendations', 'decision_evidence', 'decision_transitions', 'actions',
    'kpi_definitions', 'kpi_values', 'decision_outcomes', 'lessons_learned',
    'notifications', 'policy_results'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I
         USING (tenant_id = app_current_tenant())
         WITH CHECK (tenant_id = app_current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO agentic_app', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO agentic_provisioner', t);
  END LOOP;
END $$;

-- The transition log is append-only for every role, including the one that
-- provisions tenants. Revoking the grant is not enough on its own — an owner
-- can restore a grant — which is why the triggers above exist as well.
REVOKE UPDATE, DELETE, TRUNCATE ON decision_transitions FROM agentic_app;
REVOKE UPDATE, DELETE, TRUNCATE ON decision_transitions FROM agentic_provisioner;

-- A consequence worth stating rather than discovering later: because the
-- trigger fires on cascaded deletes too, a decision that has any transition
-- history cannot be removed with DELETE, and neither can its tenant. This is
-- the same property audit_events has carried since 0006, and it is intended —
-- a decision record whose history can be erased is not a record. Subject
-- erasure therefore pseudonymises here rather than deleting, exactly as it
-- does for the ledger; see NON_ERASABLE in agentic_os.privacy.dsar.
