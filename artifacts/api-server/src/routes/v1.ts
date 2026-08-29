import { randomUUID } from "node:crypto";
import { Router, type IRouter, type NextFunction, type Request, type Response } from "express";
import cookieParser from "cookie-parser";
import {
  CreateRunBody,
  CreateRunResponse,
  IngestDocumentBody,
  IngestDocumentResponse,
  LoginBody,
  LoginResponse,
  UpdateKillSwitchBody,
  UpdateKillSwitchResponse,
  VerifyAuditResponse,
} from "@workspace/api-zod";
import {
  audit,
  authenticate,
  ensureSeedIdentity,
  listAudit,
  listRecords,
  login,
  logout,
  mutateWithAudit,
  seedRecord,
  TENANT_ID,
  type Principal,
  verifyAudit,
} from "../lib/persistence";

const router: IRouter = Router();
router.use(cookieParser());

const now = () => new Date().toISOString();
const SESSION_COOKIE = "agentic_session";
const seedPrincipal = {
  user_id: "usr_01", email: "alex.morgan@northstar.example", display_name: "Alex Morgan",
  tenant_id: "tenant_northstar", organization_id: "org_northstar", roles: ["Platform Administrator", "Approver"],
  permissions: ["runs:read"], clearance: "CONFIDENTIAL", mfa_satisfied: true,
};
const agents = [
  { agent_key: "operations-analyst", name: "Operations Analyst", owner_team: "Reliability", business_purpose: "Analyse operational telemetry and recommend corrective actions.", risk_class: "MEDIUM", max_autonomy: "SUPERVISED", status: "ACTIVE", current_version: "1.4.0", contract_hash: "sha256:7c3d91a8", allowed_tools: ["metrics-query", "knowledge-search"], allowed_skills: ["incident-analysis", "trend-analysis"], allowed_models: ["local-reasoner-v2"], max_classification: "CONFIDENTIAL", cost_budget_usd: 500, max_tool_calls: 20, slo_success_rate: 0.99, run_count: 148 },
  { agent_key: "procurement-advisor", name: "Procurement Advisor", owner_team: "Finance", business_purpose: "Prepare governed supplier and spend recommendations.", risk_class: "HIGH", max_autonomy: "HUMAN_APPROVAL", status: "ACTIVE", current_version: "2.1.0", contract_hash: "sha256:94b2f020", allowed_tools: ["knowledge-search", "vendor-catalog"], allowed_skills: ["spend-analysis"], allowed_models: ["local-reasoner-v2"], max_classification: "INTERNAL", cost_budget_usd: 350, max_tool_calls: 12, slo_success_rate: 0.98, run_count: 73 },
];
const baseRun = (id: string, objective: string, status = "SUCCEEDED") => ({ id, objective, status, owner_agent_key: "operations-analyst", autonomy_level: status === "AWAITING_APPROVAL" ? "HUMAN_APPROVAL" : "SUPERVISED", risk_class: status === "AWAITING_APPROVAL" ? "HIGH" : "MEDIUM", risk_score: 42, confidence: 0.91, classification: "INTERNAL", cost_usd: 0.18, duration_ms: status === "SUCCEEDED" ? 1840 : null, error_class: status === "FAILED" ? "VALIDATION_ERROR" : null, created_at: now(), completed_at: status === "SUCCEEDED" || status === "FAILED" ? now() : null, requested_by_email: seedPrincipal.email, step_count: 3, pending_approvals: status === "AWAITING_APPROVAL" ? 1 : 0 });
const runs = [baseRun("run_001", "Identify recurring escalator failures and dominant failure mode."), baseRun("run_002", "Review proposed supplier contract changes.", "AWAITING_APPROVAL"), baseRun("run_003", "Summarise incomplete maintenance records.", "FAILED")];
const approvals = [{
  id: "apr_001", run_id: "run_002", action: "Approve supplier contract recommendation", target: "Supplier renewal proposal · Atlas Parts", status: "PENDING", mode: "SINGLE", required_approvals: 1, risk_class: "HIGH", autonomy_level: "HUMAN_APPROVAL", financial_impact_usd: 42500, reversibility: "REVERSIBLE", confidence: 0.87, reason: "Commercial commitment exceeds the agent autonomy threshold.", consequences: "Approval allows a recommendation to be sent to the procurement owner; no contract is executed.", requested_by_agent: "procurement-advisor", evidence: [{ title: "Spend analysis", verified: true }], sources: [{ document_id: "doc_001", title: "FY25 supplier spend" }], policy_refs: [{ policy_key: "commercial-approval" }], expires_at: "2026-12-31T17:00:00.000Z", created_at: now(),
}];

function runDetail(run: Record<string, unknown>) {
  return { run, plan: [{ version: 1, planner: "conductor", steps: [{ index: 1, key: "retrieve", skill: "trend-analysis", tool: "knowledge-search", description: "Retrieve governed maintenance records.", requires_approval: false }, { index: 2, key: "analyse", skill: "incident-analysis", tool: null, description: "Identify recurring failure patterns.", requires_approval: false }], plan_hash: "sha256:plan01", validated: true, validation_errors: [], rationale: "Use approved operational knowledge sources." }], steps: [{ step_index: 1, step_key: "retrieve", step_type: "TOOL", agent_key: "operations-analyst", skill_key: "trend-analysis", tool_key: "knowledge-search", status: "SUCCEEDED", attempt: 1, error_class: null, error_message: null, cost_usd: 0.03, input_tokens: 420, output_tokens: 210, latency_ms: 184, output: { documents: 3 } }], policy_decisions: [{ action: "knowledge.search", resource: "maintenance-records", effect: "ALLOW", reason: "Classification and agent grant are compatible.", evaluated_at: now() }], risk_assessments: [{ action: "analyse.operations", risk_class: "MEDIUM", risk_score: 42, factors: [{ name: "data sensitivity", weight: 0.4, detail: "Internal operational data" }], reversibility: "REVERSIBLE", required_autonomy: "SUPERVISED" }], tool_calls: [{ tool_key: "knowledge-search", agent_key: "operations-analyst", gateway_decision: "ALLOW", denial_stage: "", denial_reason: "", verification_status: "VERIFIED", latency_ms: 184 }], approvals: approvals.filter((a) => run.id === "run_002").map(({ id, action, status, mode, risk_class, reason }) => ({ id, action, status, mode, risk_class, reason })), citations: [{ chunk_id: "chunk_001", document_id: "doc_001", title: "Escalator maintenance incidents Q2", section_path: "Findings", snippet: "Repeated sensor calibration fault.", verified: true }], model_calls: [{ provider: "Replit", model_key: "local-reasoner-v2", input_tokens: 420, output_tokens: 210, cost_usd: 0.15 }], trace: [{ name: "governed-run", kind: "workflow", status: "OK", duration_ms: 1840 }], audit: [{ sequence_no: 1042, category: "RUN", action: "run.completed", outcome: String(run.status), occurred_at: now() }] };
}

async function seedMutableState() {
  await ensureSeedIdentity();
  await Promise.all([
    ...runs.map((run) => seedRecord("run", run.id, run)),
    ...approvals.map((approval) => seedRecord("approval", approval.id, approval)),
    seedRecord("document", documents[0].id, documents[0]),
    seedRecord("kill_switch", "GLOBAL:all", { scope: "GLOBAL", target_key: "", engaged: false, reason: "", engaged_at: null }),
  ]);
}

router.post("/v1/auth/login", async (req, res): Promise<void> => {
  const parsed = LoginBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ message: "Tenant, email and password are required." }); return; }
  const { tenant, email, password, mfa_code } = parsed.data;
  const session = await login(tenant, email, password, mfa_code);
  if (!session) { res.status(401).json({ message: "Invalid email or password." }); return; }
  res.cookie(SESSION_COOKIE, session.token, { httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", maxAge: 28800000, path: "/" });
  req.log.info({ userId: session.principal.user_id }, "User signed in");
  res.json(LoginResponse.parse({ authenticated: true, expires_in: 28800, principal: session.principal }));
});
router.post("/v1/auth/logout", async (req, res): Promise<void> => {
  const token = req.cookies[SESSION_COOKIE];
  if (typeof token === "string") await logout(token);
  res.clearCookie(SESSION_COOKIE, { path: "/" });
  req.log.info("User signed out");
  res.status(204).send();
});
router.use(async (req, res, next): Promise<void> => {
  const token = req.cookies[SESSION_COOKIE];
  const principal = typeof token === "string" ? await authenticate(token) : null;
  if (!principal) {
    res.status(401).json({ message: "Authentication required." });
    return;
  }
  res.locals.principal = principal;
  if (process.env.NODE_ENV !== "production" && principal.tenant_id === TENANT_ID) await seedMutableState();
  next();
});
router.get("/v1/auth/me", (_req, res): void => { res.json(res.locals.principal); });

function requirePermission(permission: string) {
  return (_req: Request, res: Response, next: NextFunction) => {
    const principal = res.locals.principal as Principal;
    if (!principal.permissions.includes(permission)) {
      res.status(403).json({ message: `Permission required: ${permission}` });
      return;
    }
    if (["approvals:decide", "security:manage"].includes(permission) && !principal.mfa_satisfied) {
      res.status(403).json({ message: "Multi-factor authentication is required for this action." });
      return;
    }
    next();
  };
}

router.use((req, res, next): void => {
  if (req.method !== "GET") { next(); return; }
  const rules: [string, string][] = [
    ["/v1/auth/me", ""], ["/v1/command-center", "platform:read"],
    ["/v1/runs", "runs:read"], ["/v1/approvals", "approvals:read"],
    ["/v1/agents", "agents:read"], ["/v1/skills", "agents:read"],
    ["/v1/models", "agents:read"], ["/v1/prompts", "agents:read"],
    ["/v1/tools", "agents:read"], ["/v1/mcp", "agents:read"],
    ["/v1/knowledge", "knowledge:read"], ["/v1/documents", "documents:read"],
    ["/v1/datasets", "knowledge:read"], ["/v1/graph", "knowledge:read"],
    ["/v1/evidence", "governance:read"], ["/v1/policies", "governance:read"],
    ["/v1/audit", "governance:read"], ["/v1/privacy", "governance:read"],
    ["/v1/risks", "governance:read"], ["/v1/security", "security:read"],
    ["/v1/analytics", "platform:read"], ["/v1/costs", "platform:read"],
    ["/v1/outcomes", "platform:read"], ["/v1/incidents", "platform:read"],
    ["/v1/workflows", "platform:read"], ["/v1/resilience", "platform:read"],
    ["/v1/organization", "organization:read"], ["/v1/capabilities", "platform:read"],
  ];
  const permission = rules.find(([prefix]) => req.path.startsWith(prefix))?.[1];
  if (permission === undefined) {
    res.status(403).json({ message: "This route has no authorization policy." });
    return;
  }
  if (permission && !(res.locals.principal as Principal).permissions.includes(permission)) {
    res.status(403).json({ message: `Permission required: ${permission}` });
    return;
  }
  next();
});

router.get("/v1/command-center", async (_req, res) => {
  const killSwitches = await listRecords<{ engaged: boolean }>((res.locals.principal as Principal).tenant_id, "kill_switch");
  const engagedKillSwitches = killSwitches.filter((item) => item.engaged);
  res.json({
  requires_attention: { pending_approvals: approvals.filter((a) => a.status === "PENDING"), failed_runs: runs.filter((r) => r.status === "FAILED"), security_findings: [{ finding_type: "Blocked prompt injection", severity: "HIGH", source: "tool-gateway", created_at: now() }], open_incidents: [{ incident_key: "INC-042", title: "Elevated sensor data latency", severity: "MEDIUM", status: "INVESTIGATING" }], dead_letters: 0, expired_evidence: 1 },
  agent_operations: { runs: { total: runs.length, succeeded: 1, failed: 1, success_rate: 0.5, p95_duration_ms: 1840 }, tools: { total: 26, denied: 2, denial_rate: 0.077 }, retrieval: { queries: 12, chunks_withheld_by_acl: 3 }, security: { findings: 1, severe_findings: 1 }, policy: { decisions: 28, denied: 2, escalated_to_approval: 1 } },
  business_pulse: { runs_total: runs.length, runs_succeeded: 1, runs_awaiting_approval: 1, success_rate: 0.5, cost_usd: 31.42 }, engaged_kill_switches: engagedKillSwitches, read_only_mode: engagedKillSwitches.some((item) => item.engaged),
  });
});
router.get("/v1/runs", async (req, res): Promise<void> => { const status = typeof req.query.status === "string" ? req.query.status : ""; const tenantId = (res.locals.principal as Principal).tenant_id; const stored = await listRecords<typeof runs[number]>(tenantId, "run"); res.json({ runs: status ? stored.filter((r) => r.status === status) : stored }); });
router.post("/v1/runs", requirePermission("runs:create"), async (req, res): Promise<void> => {
  const parsed = CreateRunBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ message: "Objective must be at least 3 characters." }); return; }
  const objective = parsed.data.objective.trim();
  const principal = res.locals.principal as Principal;
  const killSwitches = await listRecords<{ engaged: boolean; scope: string; target_key: string }>(principal.tenant_id, "kill_switch");
  if (killSwitches.some((item) => item.engaged && (item.scope === "GLOBAL" || (item.scope === "AGENT" && item.target_key === "operations-analyst")))) {
    await audit(res.locals.principal, "RUN", "run.blocked", "DENIED", "run", "new", { reason: "kill_switch" });
    res.status(423).json({ message: "Run creation is blocked by an engaged kill switch." });
    return;
  }
  const approvalRequired = /contract|purchase|supplier|payment|delete|send/i.test(objective);
  const run = baseRun(`run_${randomUUID().slice(0, 8)}`, objective, approvalRequired ? "AWAITING_APPROVAL" : "SUCCEEDED");
  const records: { type: string; id: string; data: unknown }[] = [{ type: "run", id: run.id, data: run }];
  if (approvalRequired) { const approval = { ...approvals[0], id: `apr_${randomUUID().slice(0, 8)}`, run_id: run.id, status: "PENDING", action: `Approve: ${objective.slice(0, 80)}`, created_at: now() }; records.push({ type: "approval", id: approval.id, data: approval }); }
  await mutateWithAudit(principal, records, { category: "RUN", action: "run.created", outcome: run.status, resourceType: "run", resourceId: run.id, detail: { objective } });
  req.log.info({ runId: run.id }, "Created demo run");
  res.status(201).json(CreateRunResponse.parse({ run_id: run.id, status: run.status, validation: { issues: [] } }));
});
router.get("/v1/runs/:id", async (req, res): Promise<void> => { const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id; const stored = await listRecords<typeof runs[number]>((res.locals.principal as Principal).tenant_id, "run"); const run = stored.find((item) => item.id === id); if (!run) { res.status(404).json({ message: "Run not found" }); return; } res.json(runDetail(run)); });
router.get("/v1/approvals", async (_req, res) => res.json({ approvals: await listRecords((res.locals.principal as Principal).tenant_id, "approval") }));
router.post("/v1/approvals/:id/decide", requirePermission("approvals:decide"), async (req, res): Promise<void> => {
  const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
  const principal = res.locals.principal as Principal;
  const storedApprovals = await listRecords<typeof approvals[number]>(principal.tenant_id, "approval");
  const approval = storedApprovals.find((item) => item.id === id);
  if (!approval) { res.status(404).json({ message: "Approval not found" }); return; }
  if (approval.status !== "PENDING") {
    res.status(409).json({ message: "This approval has already been decided." });
    return;
  }
  if (new Date(approval.expires_at).getTime() <= Date.now()) {
    res.status(409).json({ message: "This approval has expired and cannot be decided." });
    return;
  }
  const decision = String(req.body?.decision ?? req.body?.status ?? "").toUpperCase();
  if (!["APPROVED", "REJECTED", "CHANGES_REQUESTED"].includes(decision)) {
    res.status(400).json({ message: "Decision must be APPROVED, REJECTED, or CHANGES_REQUESTED." });
    return;
  }
  approval.status = decision;
  const storedRuns = await listRecords<typeof runs[number]>(principal.tenant_id, "run");
  const heldRun = storedRuns.find((run) => run.id === approval.run_id);
  if (heldRun) {
    heldRun.status = decision === "APPROVED" ? "SUCCEEDED" : decision === "REJECTED" ? "CANCELLED" : "AWAITING_APPROVAL";
    heldRun.pending_approvals = decision === "CHANGES_REQUESTED" ? 1 : 0;
  }
  await mutateWithAudit(principal, [
    { type: "approval", id: approval.id, data: approval },
    ...(heldRun ? [{ type: "run", id: heldRun.id, data: heldRun }] : []),
  ], { category: "APPROVAL", action: "approval.decided", outcome: decision, resourceType: "approval", resourceId: id, detail: { run_id: approval.run_id } });
  req.log.info({ approvalId: id, decision }, "Approval decided");
  res.json(approval);
});

router.get("/v1/agents", (_req, res) => res.json({ agents }));
router.get("/v1/agents/:key", async (req, res): Promise<void> => { const key = Array.isArray(req.params.key) ? req.params.key[0] : req.params.key; const agent = agents.find((item) => item.agent_key === key); if (!agent) { res.status(404).json({ message: "Agent not found" }); return; } const storedRuns = await listRecords<typeof runs[number]>((res.locals.principal as Principal).tenant_id, "run"); res.json({ agent: { ...agent, contract: { purpose: { business_purpose: agent.business_purpose, allowed: ["Analyse governed operational records", "Recommend reversible corrective actions"], prohibited: ["Execute physical control actions", "Access restricted HR records"] }, tools: { allowed: agent.allowed_tools, denied: ["contract-execute"] }, skills: { allowed: agent.allowed_skills }, data: { permitted_domains: ["maintenance", "operations"], prohibited_domains: ["payroll", "health"] }, autonomy: { consequential_actions: ["supplier.commitment", "external.communication"] }, limits: { token_budget: 32000, cost_budget_usd: agent.cost_budget_usd, max_runtime_seconds: 300, max_tool_calls: agent.max_tool_calls }, requirements: { citations: true, provenance: true, evaluation: { min_score: 0.9, suites: ["operational-safety"] } } }, published_at: "2025-06-01T00:00:00.000Z" }, recent_runs: storedRuns.filter((run) => run.owner_agent_key === key).slice(0, 5), evaluations: [{ suite_key: "operational-safety", score: 0.96, threshold: 0.9, passed: true, case_count: 120, created_at: now() }] }); });
router.get("/v1/skills", (_req, res) => res.json({ skills: [{ skill_key: "trend-analysis", name: "Trend analysis", description: "Finds repeated operational patterns.", owner_team: "Reliability", execution_mode: "MODEL_BACKED", risk_class: "MEDIUM", status: "ACTIVE", required_tools: ["knowledge-search"], evaluation_threshold: 0.9 }, { skill_key: "spend-analysis", name: "Spend analysis", description: "Calculates supplier spend variance.", owner_team: "Finance", execution_mode: "DETERMINISTIC", risk_class: "HIGH", status: "ACTIVE", required_tools: ["vendor-catalog"], evaluation_threshold: 0.95 }] }));
router.get("/v1/models", (_req, res) => res.json({ models: [{ model_key: "local-reasoner-v2", provider: "Replit", deployment: "managed", capabilities: ["analysis", "summarization"], max_classification: "CONFIDENTIAL", context_window: 32768, input_cost_per_1k: 0.001, output_cost_per_1k: 0.002, p95_latency_ms: 850, evaluation_score: 0.94, known_limitations: "Does not execute actions without a governed tool.", residency: "us-east", approval_state: "APPROVED" }], usage: [{ model_key: "local-reasoner-v2", calls: 148, tokens: 284000, cost: 31.42 }] }));
router.get("/v1/prompts", (_req, res) => res.json({ prompts: [{ prompt_key: "operations-analysis", purpose: "Operational trend analysis", owning_agent_key: "operations-analyst", current_version: "1.4.0", version: "1.4.0", deployment_status: "PUBLISHED", body_hash: "sha256:ac19", evaluation_score: 0.96, effective_from: "2025-06-01T00:00:00.000Z" }] }));
router.get("/v1/tools", (_req, res) => res.json({ tools: [{ tool_key: "knowledge-search", name: "Knowledge search", description: "Searches ACL-filtered enterprise knowledge.", kind: "INTERNAL", connector_key: "knowledge", side_effect: "READ_ONLY", reversibility: "REVERSIBLE", risk_class: "MEDIUM", min_autonomy: "SUPERVISED", requires_approval: false, verification_mode: "EVIDENCE_REQUIRED", implementation_status: "IMPLEMENTED", status: "ACTIVE" }] }));
router.get("/v1/mcp", (_req, res) => res.json({ servers: [{ server_key: "maintenance-mcp", name: "Maintenance knowledge", provider: "Northstar", endpoint: "https://mcp.northstar.example/maintenance", transport: "HTTPS", trust_class: "TRUSTED", authorization_method: "WORKLOAD_IDENTITY", data_classification: "INTERNAL", allowed_agents: ["operations-analyst"], forward_user_token: false, status: "ACTIVE", last_security_review: "2025-05-01T00:00:00.000Z", last_used_at: now(), tool_count: 4, approved_tool_count: 4 }] }));

const documents = [{ id: "doc_001", title: "Escalator maintenance incidents Q2", source_system: "SharePoint", mime_type: "application/pdf", byte_size: 248320, classification: "INTERNAL", owner_team: "Reliability", ingest_status: "COMPLETE", malware_scan_status: "CLEAN", dlp_labels: ["operational"], parse_confidence: 0.98, unsupported_elements: [], chunk_count: 18, created_at: "2025-06-12T09:30:00.000Z" }];
router.post("/v1/knowledge/search", requirePermission("knowledge:read"), async (req, res): Promise<void> => {
  const query = String(req.body?.query ?? req.body?.q ?? "");
  const principal = res.locals.principal as Principal;
  const storedDocuments = await listRecords<(typeof documents)[number] & { content?: string }>(principal.tenant_id, "document");
  const classificationRank: Record<string, number> = { PUBLIC: 0, INTERNAL: 1, CONFIDENTIAL: 2, RESTRICTED: 3 };
  const visibleDocuments = storedDocuments.filter((document) =>
    (classificationRank[document.classification] ?? 99) <= (classificationRank[principal.clearance] ?? 0),
  );
  const normalized = query.toLowerCase();
  const matches = visibleDocuments
    .filter((document) => !normalized || `${document.title} ${document.content ?? ""}`.toLowerCase().includes(normalized))
    .slice(0, 10)
    .map((document, index) => ({
      chunk_id: `${document.id}_chunk_1`, document_id: document.id, title: document.title,
      section: "Ingested content", snippet: document.content?.slice(0, 260) ?? "Sensor calibration faults accounted for 42% of recurring escalator incidents.",
      score: Math.max(0.7, 0.94 - index * 0.03), classification: document.classification, verified: true,
    }));
  res.json({
    query,
    results: matches,
    candidates_before_acl: storedDocuments.length,
    candidates_after_acl: visibleDocuments.length,
    acl_filtered_count: storedDocuments.length - visibleDocuments.length,
    clearance_ceiling: (res.locals.principal as Principal).clearance,
    latency_ms: 24,
  });
});
router.get("/v1/documents", async (_req, res) => {
  const principal = res.locals.principal as Principal;
  const rows = await listRecords<Record<string, unknown>>(principal.tenant_id, "document");
  res.json({ documents: rows.map(({ content: _content, ...metadata }) => metadata) });
});
router.post("/v1/documents", requirePermission("documents:write"), async (req, res): Promise<void> => {
  const parsed = IngestDocumentBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ message: "A title and at least 10 characters of document text are required." });
    return;
  }
  const { title, content, classification } = parsed.data;
  const id = `doc_${randomUUID().slice(0, 8)}`;
  const document = {
    id, title, source_system: "Direct upload", mime_type: "text/plain",
    byte_size: Buffer.byteLength(content), classification,
    owner_team: "Platform", ingest_status: "COMPLETE", malware_scan_status: "CLEAN",
    dlp_labels: [], parse_confidence: 1, unsupported_elements: [],
    chunk_count: Math.max(1, Math.ceil(content.length / 800)), created_at: now(), content,
  };
  await mutateWithAudit(res.locals.principal, [{ type: "document", id, data: document }], {
    category: "KNOWLEDGE", action: "document.ingested", outcome: "SUCCEEDED",
    resourceType: "document", resourceId: id, detail: { title },
  });
  res.status(201).json(IngestDocumentResponse.parse({ document }));
});
router.get("/v1/datasets", (_req, res) => res.json({ datasets: [{ dataset_key: "maintenance_incidents", name: "Maintenance incidents", description: "Governed normalized maintenance records.", source_system: "CMMS export", owner_team: "Reliability", classification: "INTERNAL", row_count: 18422, quality_score: 0.97, quality_detail: { dimensions: { completeness: 0.98, freshness: 0.96 } }, freshness_at: now(), primary_key_field: "incident_id" }] }));
router.get("/v1/graph", (_req, res) => res.json({ nodes: [{ node_key: "asset:escalator-14", node_type: "ASSET", label: "Escalator 14", classification: "INTERNAL", confidence: 0.98, source_ref: "maintenance_incidents" }, { node_key: "failure:sensor-calibration", node_type: "FAILURE_MODE", label: "Sensor calibration fault", classification: "INTERNAL", confidence: 0.94, source_ref: "doc_001" }], edges: [{ from: "asset:escalator-14", to: "failure:sensor-calibration", relation: "EXHIBITS", confidence: 0.94 }] }));
router.get("/v1/graph/impact", (req, res) => res.json({ affected: [{ node_key: "failure:sensor-calibration", node_type: "FAILURE_MODE", label: "Sensor calibration fault", distance: 1 }], affected_count: 1, note: `Impact is limited to the governed graph neighbourhood of ${String(req.query.node_key ?? "asset:escalator-14")}.` }));

router.get("/v1/evidence", (_req, res) => res.json({ available: true, score: 91, certified: false, critical_blockers: [], domain_scores: { security: { score: 94, applicable_weight: 30, verified_weight: 28, passed: 7, failed: 0, not_evidenced: 1 }, governance: { score: 88, applicable_weight: 25, verified_weight: 22, passed: 5, failed: 0, not_evidenced: 1 } }, controls: [{ control_id: "SEC-01", domain: "security", title: "Tool gateway enforcement", weight: 10, critical: true, applicable: true, status: "PASSED", test_id: "evidence_001", reason: "Gateway audit evidence verified." }], environment: "demo", commit_sha: "demo-port", generated_at: now() }));
router.get("/v1/policies", (_req, res) => res.json({ policies: [{ policy_key: "commercial-approval", name: "Commercial commitment approval", description: "Requires a human decision for material commercial actions.", category: "AUTONOMY", owner_team: "Risk", enforcement: "BLOCKING", status: "ACTIVE", current_version: 3, rules: [{ name: "high-value commitment", effect: "ESCALATE", reason: "Financial impact exceeds threshold." }], rules_hash: "sha256:policy01" }] }));
router.get("/v1/policies/decisions", (_req, res) => res.json({ decisions: [{ action: "knowledge.search", resource: "maintenance-records", effect: "ALLOW", reason: "Agent grant verified.", evaluated_at: now() }, { action: "supplier.recommendation", resource: "atlas-parts", effect: "ESCALATE", reason: "Human approval required.", evaluated_at: now() }] }));
router.get("/v1/risks", (_req, res) => res.json({ risk_assessments: [{ action: "supplier.recommendation", risk_class: "HIGH", risk_score: 0.74, factors: [{ name: "financial impact", weight: 0.6, detail: "Potential annual commitment of $42,500." }], reversibility: "REVERSIBLE", financial_impact_usd: 42500, required_autonomy: "HUMAN_APPROVAL", assessed_at: now() }] }));
router.get("/v1/audit", async (_req, res) => res.json({ events: await listAudit((res.locals.principal as Principal).tenant_id) }));
router.get("/v1/audit/verify", async (_req, res) => res.json(VerifyAuditResponse.parse(await verifyAudit((res.locals.principal as Principal).tenant_id))));
router.get("/v1/privacy", (_req, res) => res.json({ requests: [{ id: "dsar_001", request_type: "ACCESS", subject_email: "employee@northstar.example", status: "IN_PROGRESS", due_at: "2025-07-31T00:00:00.000Z", completed_at: null, affected_records: { documents: 2 }, created_at: now() }], legal_holds: [{ hold_key: "hold_legal_001", reason: "Active employment matter", resource_type: "DOCUMENT", active: true, created_at: "2025-05-01T00:00:00.000Z", released_at: null }], processing_activities: [{ activity: "Operational analysis", purpose: "Reliability management", legal_basis: "Legitimate interest", data_categories: ["work contact data"], recipients: ["Reliability team"], cross_border: false, retention: "365 days", controller: "Northstar Operations" }], pii_summary: [{ pii_type: "EMAIL_ADDRESS", occurrences: 42, redacted: 42 }] }));
router.get("/v1/security", async (_req, res) => res.json({ findings: [{ finding_type: "Blocked prompt injection", severity: "HIGH", source: "tool-gateway", detail: { rule: "untrusted-instruction" }, blocked: true, created_at: now() }], kill_switches: await listRecords((res.locals.principal as Principal).tenant_id, "kill_switch"), denials_by_stage: [{ denial_stage: "POLICY", n: 2 }, { denial_stage: "CLASSIFICATION", n: 1 }] }));
router.post("/v1/security/kill-switches", requirePermission("security:manage"), async (req, res): Promise<void> => {
  const parsed = UpdateKillSwitchBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ message: "Invalid kill-switch update." }); return; }
  const scope = parsed.data.scope.toUpperCase();
  const targetKey = parsed.data.target_key;
  const engaged = parsed.data.engaged;
  const reason = parsed.data.reason.trim();
  if (!["GLOBAL", "AGENT"].includes(scope) || (scope === "GLOBAL" && targetKey)) {
    res.status(400).json({ message: "Kill-switch scope or target is invalid." });
    return;
  }
  if (scope === "AGENT" && !agents.some((agent) => agent.agent_key === targetKey)) {
    res.status(400).json({ message: "The target agent does not exist." });
    return;
  }
  if (engaged && reason.length < 5) { res.status(400).json({ message: "A reason of at least 5 characters is required." }); return; }
  const id = `${scope}:${targetKey || "all"}`;
  const state = { scope, target_key: targetKey, engaged, reason, engaged_at: engaged ? now() : null };
  await mutateWithAudit(res.locals.principal, [{ type: "kill_switch", id, data: state }], {
    category: "SECURITY", action: engaged ? "kill_switch.engaged" : "kill_switch.released",
    outcome: "SUCCEEDED", resourceType: "kill_switch", resourceId: id, detail: { reason },
  });
  res.json(UpdateKillSwitchResponse.parse({ kill_switch: state }));
});

router.get("/v1/analytics", (_req, res) => res.json({ window_hours: 168, runs: { total: 148, succeeded: 142, failed: 3, awaiting_approval: 3, p50_duration_ms: 920, p95_duration_ms: 1840 }, tools: { calls: 682, denied: 9, avg_latency_ms: 142 }, retrieval: { queries: 310, avg_latency_ms: 97, chunks_withheld_by_acl: 12 }, security: { findings: 4, severe_findings: 1 }, policy: { decisions: 706, denied: 9, escalated_to_approval: 3 }, otel_endpoint_configured: false }));
router.get("/v1/costs", (_req, res) => res.json({ window_days: 30, spend_today_usd: 4.18, by_model: [{ model_key: "local-reasoner-v2", calls: 148, input_tokens: 184000, output_tokens: 92000, cost_usd: 31.42 }], by_agent: [{ agent_key: "operations-analyst", calls: 102, cost_usd: 20.16 }, { agent_key: "procurement-advisor", calls: 46, cost_usd: 11.26 }], budgets: [{ agent_key: "operations-analyst", budget_usd: 500, spent_usd: 20.16, remaining_usd: 479.84 }] }));
router.get("/v1/outcomes", (_req, res) => res.json({ window_days: 30, platform_cost_usd: 31.42, measured_value_usd: 1820, net_value_usd: 1788.58, roi_ratio: 56.92, measured: [{ outcome_type: "Hours saved", quantity: 28, value: 1680, records: 12 }], estimated: [{ outcome_type: "Avoided downtime", quantity: 1, value: 140, records: 1 }], basis_note: "Measured from approved operational outcome records.", monetisation_note: "Estimated benefits are presented separately from measured value." }));
router.get("/v1/incidents", (_req, res) => res.json({ incidents: [{ incident_key: "INC-042", title: "Elevated sensor data latency", description: "Retriever responses are intermittently delayed.", severity: "MEDIUM", status: "INVESTIGATING", category: "PERFORMANCE", root_cause: "Under investigation", detected_at: now(), resolved_at: null }], alerts: [{ alert_type: "LATENCY_THRESHOLD", severity: "MEDIUM", title: "Retriever latency threshold exceeded", source: "observability", acknowledged_at: null, created_at: now() }] }));
router.get("/v1/workflows", (_req, res) => res.json({ workflows: [{ workflow_key: "governed-analysis", name: "Governed analysis", description: "Plan, validate, retrieve, analyse and record evidence.", owner_team: "Platform", status: "ACTIVE", current_version: 1, max_concurrent_runs: 10, definition: { steps: [{ key: "plan", type: "PLAN" }, { key: "retrieve", type: "TOOL_CALL" }] }, definition_hash: "sha256:workflow01", run_count: runs.length }], step_types: ["PLAN", "POLICY_CHECK", "TOOL_CALL", "MODEL_CALL", "APPROVAL"] }));
router.get("/v1/workflows/runs", (_req, res) => res.json({ workflow_runs: runs.map((run) => ({ id: `wfr_${run.id}`, workflow_key: "governed-analysis", status: run.status, current_step: run.status === "SUCCEEDED" ? 2 : 1, paused: run.status === "AWAITING_APPROVAL", error_class: run.error_class, started_at: run.created_at })) }));
router.get("/v1/resilience", (_req, res) => res.json({ backups: [{ backup_type: "DATABASE", scope: "tenant_northstar", artifact_hash: "sha256:backup001", size_bytes: 2480000, status: "VERIFIED", started_at: "2025-06-15T01:00:00.000Z", completed_at: "2025-06-15T01:04:00.000Z" }], restore_tests: [{ environment: "staging", outcome: "SUCCEEDED", rpo_achieved_seconds: 300, rto_achieved_seconds: 840, verified_rows: 18422, notes: "Quarterly restore exercise.", executed_by: "platform-ops", executed_at: "2025-06-01T10:00:00.000Z" }] }));
router.get("/v1/organization", (_req, res) => { const principal = res.locals.principal as Principal; res.json({ tenant: { slug: "northstar-demo", name: "Northstar Enterprise", region: "us-east", data_residency: "United States", default_classification: "INTERNAL", retention_days: 365, daily_cost_cap_usd: 1000, status: "ACTIVE", org_slug: "northstar", org_name: "Northstar Operations" }, users: [{ email: principal.email, display_name: principal.display_name, clearance: principal.clearance, status: "ACTIVE", mfa_enrolled: true, last_login_at: now(), roles: principal.roles }] }); });
router.get("/v1/capabilities", (_req, res) => res.json({ agents: agents.map((agent) => agent.agent_key), skills: { deterministic: ["spend-analysis"], model_backed: ["trend-analysis", "incident-analysis"] }, tools: { implemented: ["knowledge-search", "metrics-query", "vendor-catalog"], declared_not_implemented: ["email-send", "contract-execute"] }, models: { "local-reasoner-v2": { provider: "Replit", deployment: "managed", approval_state: "APPROVED" } }, external_model_providers_enabled: false, embedding_provider: "local", policy_mode: "ENFORCING", kms_backend: "managed", secret_backend: "managed" }));

export default router;