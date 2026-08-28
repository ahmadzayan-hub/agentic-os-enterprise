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
