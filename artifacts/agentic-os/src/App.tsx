import { type ReactNode } from 'react';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import NotFound from '@/pages/not-found';
import { Route, Switch, useLocation, Router as WouterRouter } from 'wouter';

import { AuthShell } from '@/components/auth-shell';

import LoginPage from '@/pages/login/index';
import HomePage from '@/pages/index';
import RunsPage from '@/pages/runs/index';
import RunPage from '@/pages/runs/runId/index';
import ApprovalsPage from '@/pages/approvals/index';
import AgentsPage from '@/pages/agents/index';
import AgentPage from '@/pages/agents/agentKey/index';
import SkillsPage from '@/pages/agents/skills/index';
import ModelsPage from '@/pages/agents/models/index';
import PromptsPage from '@/pages/agents/prompts/index';
import ToolsPage from '@/pages/agents/tools/index';
import McpPage from '@/pages/agents/mcp/index';
import KnowledgePage from '@/pages/knowledge/index';
import DocumentsPage from '@/pages/knowledge/documents/index';
import DatasetsPage from '@/pages/knowledge/datasets/index';
import GraphPage from '@/pages/knowledge/graph/index';
import EvidencePage from '@/pages/governance/evidence/index';
import PoliciesPage from '@/pages/governance/policies/index';
import RisksPage from '@/pages/governance/risks/index';
import AuditPage from '@/pages/governance/audit/index';
import PrivacyPage from '@/pages/governance/privacy/index';
import SecurityPage from '@/pages/security/index';
import AnalyticsPage from '@/pages/operations/analytics/index';
import CostsPage from '@/pages/operations/costs/index';
import OutcomesPage from '@/pages/operations/outcomes/index';
import IncidentsPage from '@/pages/operations/incidents/index';
import WorkflowsPage from '@/pages/operations/workflows/index';
import ResiliencePage from '@/pages/operations/resilience/index';
import OrganizationPage from '@/pages/operations/organization/index';
import CapabilitiesPage from '@/pages/operations/capabilities/index';

function Router() {
  return (
    <RoutedErrorBoundary>
      <Switch>
        <Route path="/login" component={LoginPage} />
        
        {/* All other routes require auth */}
        <Route path="*">
          <AuthShell>
            <Switch>
              <Route path="/" component={HomePage} />
              
              <Route path="/runs" component={RunsPage} />
              <Route path="/runs/:runId" component={RunPage} />
              
              <Route path="/approvals" component={ApprovalsPage} />
              
              <Route path="/agents" component={AgentsPage} />
              <Route path="/agents/skills" component={SkillsPage} />
              <Route path="/agents/models" component={ModelsPage} />
              <Route path="/agents/prompts" component={PromptsPage} />
              <Route path="/agents/tools" component={ToolsPage} />
              <Route path="/agents/mcp" component={McpPage} />
              <Route path="/agents/:agentKey" component={AgentPage} />
              
              <Route path="/knowledge" component={KnowledgePage} />
              <Route path="/knowledge/documents" component={DocumentsPage} />
              <Route path="/knowledge/datasets" component={DatasetsPage} />
              <Route path="/knowledge/graph" component={GraphPage} />
              
              <Route path="/governance/evidence" component={EvidencePage} />
              <Route path="/governance/policies" component={PoliciesPage} />
              <Route path="/governance/risks" component={RisksPage} />
              <Route path="/governance/audit" component={AuditPage} />
              <Route path="/governance/privacy" component={PrivacyPage} />
              
              <Route path="/security" component={SecurityPage} />
              
              <Route path="/operations/analytics" component={AnalyticsPage} />
              <Route path="/operations/costs" component={CostsPage} />
              <Route path="/operations/outcomes" component={OutcomesPage} />
              <Route path="/operations/incidents" component={IncidentsPage} />
              <Route path="/operations/workflows" component={WorkflowsPage} />
              <Route path="/operations/resilience" component={ResiliencePage} />
              <Route path="/operations/organization" component={OrganizationPage} />
              <Route path="/operations/capabilities" component={CapabilitiesPage} />
              
              <Route component={NotFound} />
            </Switch>
          </AuthShell>
        </Route>
      </Switch>
    </RoutedErrorBoundary>
  );
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function App() {
  return (
    <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
      <Router />
      <Toaster />
    </WouterRouter>
  );
}

export default App;
