export interface Principal {
  user_id: string;
  email: string;
  display_name: string;
  tenant_id: string;
  organization_id: string;
  roles: string[];
  permissions: string[];
  clearance: string;
  mfa_satisfied: boolean;
}

export interface RunSummary {
  id: string;
  objective: string;
  status: string;
  owner_agent_key: string;
  autonomy_level: string;
  risk_class: string;
  risk_score: number | null;
  confidence: number | null;
  classification: string;
  cost_usd: number;
  duration_ms: number | null;
  error_class: string | null;
  created_at: string;
  completed_at: string | null;
  requested_by_email: string | null;
  step_count: number;
  pending_approvals: number;
}

export interface ApprovalCard {
  id: string;
  action: string;
  target: string;
  status?: string;
  mode: string;
  required_approvals: number;
  risk_class: string;
  autonomy_level: string;
  financial_impact_usd: number;
  reversibility: string;
  confidence: number | null;
  reason: string;
  consequences: string;
  requested_by_agent: string;
  evidence: unknown[];
  sources: unknown[];
  policy_refs: unknown[];
  expires_at: string;
  created_at: string;
}

export interface MaturityControl {
  control_id: string;
  domain: string;
  title: string;
  weight: number;
  critical: boolean;
  applicable: boolean;
  status: string;
  test_id: string;
  reason?: string;
}

export interface MaturityReport {
  available: boolean;
  message?: string;
  score: number;
  certified: boolean;
  critical_blockers: string[];
  domain_scores: Record<
    string,
    {
      score: number;
      applicable_weight: number;
      verified_weight: number;
      passed: number;
      failed: number;
      not_evidenced: number;
    }
  >;
  controls: MaturityControl[];
  environment: string;
  commit_sha: string;
  generated_at: string;
}

// ------------------------------------------------------------ decisions
//
// `confidence` and `rate` are nullable throughout, and that is load-bearing
// rather than defensive: null means the platform could not defensibly compute
// a figure, and every surface must render the words "Not Calculated" rather
// than substituting zero. Typing them as `number` with a default would put the
// substitution one careless line away.

export interface DecisionSummary {
  id: string;
  reference: string;
  title: string;
  summary: string;
  state: string;
  risk: string;
  classification: string;
  detected_by: string;
  due_at: string | null;
  created_at: string;
  updated_at: string;
  domain_slug: string;
  domain_name: string;
  owner_email: string | null;
}

export interface DecisionOption {
  id: string;
  label: string;
  description: string;
  score: number | null;
  estimated_cost: number | null;
  currency: string;
  risk: string;
  reversible: boolean;
  is_status_quo: boolean;
}

export interface ConfidenceInput {
  name: string;
  raw: number;
  normalised: number;
  weight: number;
}

export interface Confidence {
  value: number | null;
  display: string;
  calculation: {
    method?: string;
    weights?: Record<string, number>;
    inputs: ConfidenceInput[];
    reason?: string;
  };
}

export interface Recommendation {
  id: string;
  option_id: string | null;
  rationale: string;
  reasoning_summary: string;
  produced_by: string;
  confidence: number | null;
  created_at: string;
}

export interface DecisionEvidence {
  id: string;
  source_kind: string;
  source_ref: string;
  summary: string;
  authority_weight: number;
  observed_at: string;
}

export interface DecisionTransition {
  id: string;
  from_state: string | null;
  to_state: string;
  actor_kind: string;
  reason: string;
  occurred_at: string;
}

export interface DecisionAction {
  id: string;
  title: string;
  action_kind: string;
  status: string;
  reversible: boolean;
  reversal_plan: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface DecisionOutcome {
  id: string;
  kpi_definition_id: string | null;
  target_value: number | null;
  actual_value: number | null;
  unit: string;
  verdict: string;
  verification_method: string;
  verified_at: string | null;
  notes: string;
}

export interface LessonLearned {
  id: string;
  lesson: string;
  category: string;
  created_at: string;
}

export interface DecisionCase extends DecisionSummary {
  domain_id: string;
  detection_source: string;
  closed_at: string | null;
  options: DecisionOption[];
  recommendation: Recommendation | null;
  evidence: DecisionEvidence[];
  transitions: DecisionTransition[];
  actions: DecisionAction[];
  outcomes: DecisionOutcome[];
  lessons: LessonLearned[];
  confidence: Confidence;
}

export interface DecisionQueue {
  items: DecisionSummary[];
  count: number;
  scope: { domains: string[]; sees_all_domains: boolean };
}

export interface Effectiveness {
  rate: number | null;
  display: string;
  achieved: number;
  verified: number;
  reached_verification: number;
  unverifiable: number;
  in_flight: number;
  definition: string;
}

export interface LifecycleGraph {
  states: string[];
  transitions: Record<string, string[]>;
}

export interface KpiDefinition {
  id: string;
  kpi_key: string;
  name: string;
  description: string;
  formula: string;
  unit: string;
  direction: "UP_IS_GOOD" | "DOWN_IS_GOOD";
  target_value: number | null;
  warning_value: number | null;
  status: string;
  latest_value: number | null;
  latest_period_end: string | null;
  latest_basis: string | null;
}

export interface NotificationItem {
  id: string;
  kind: string;
  subject: string;
  body: string;
  read_at: string | null;
  created_at: string;
  decision_id: string | null;
  decision_reference: string | null;
  decision_state: string | null;
}
