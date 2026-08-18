-- 0005 Knowledge plane: documents, ACLs, chunks, embeddings, knowledge graph.

CREATE TABLE documents (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  organization_id    uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  external_ref       text NOT NULL DEFAULT '',
  title              text NOT NULL,
  source_system      text NOT NULL DEFAULT 'upload',
  mime_type          text NOT NULL DEFAULT 'application/octet-stream',
  byte_size          bigint NOT NULL DEFAULT 0,
  content_hash       text NOT NULL,
  storage_uri        text NOT NULL DEFAULT '',
  classification     data_classification NOT NULL DEFAULT 'INTERNAL',
  owner_user_id      uuid REFERENCES users(id),
  owner_team         text NOT NULL DEFAULT '',
  language           text NOT NULL DEFAULT 'und',
  ingest_status      text NOT NULL DEFAULT 'QUARANTINED'
                     CHECK (ingest_status IN
                            ('QUARANTINED', 'SCANNING', 'PARSING', 'ENRICHING',
                             'INDEXING', 'PUBLISHED', 'REJECTED', 'FAILED')),
  ingest_stage_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  rejection_reason   text NOT NULL DEFAULT '',
  malware_scan_status text NOT NULL DEFAULT 'PENDING'
                     CHECK (malware_scan_status IN ('PENDING', 'CLEAN', 'INFECTED', 'ERROR', 'SKIPPED')),
  pii_findings       jsonb NOT NULL DEFAULT '[]'::jsonb,
  dlp_labels         text[] NOT NULL DEFAULT '{}',
  parse_confidence   numeric(5,4),
  unsupported_elements text[] NOT NULL DEFAULT '{}',
  page_count         integer,
  retention_until    timestamptz,
  legal_hold         boolean NOT NULL DEFAULT false,
  current_version    integer NOT NULL DEFAULT 1,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz
);
CREATE INDEX documents_tenant_status_idx ON documents(tenant_id, ingest_status);
CREATE UNIQUE INDEX documents_content_idx ON documents(tenant_id, content_hash) WHERE deleted_at IS NULL;
CREATE INDEX documents_title_trgm_idx ON documents USING gin (title gin_trgm_ops);

CREATE TABLE document_versions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version      integer NOT NULL,
  content_hash text NOT NULL,
  storage_uri  text NOT NULL DEFAULT '',
  byte_size    bigint NOT NULL DEFAULT 0,
  created_by   uuid REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, version)
);

-- Access control entries are inherited by every chunk of the document.
CREATE TABLE document_acl (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  principal_type text NOT NULL CHECK (principal_type IN ('USER', 'GROUP', 'ROLE', 'AGENT', 'PUBLIC')),
  principal_id text NOT NULL,
  permission   text NOT NULL DEFAULT 'READ' CHECK (permission IN ('READ', 'WRITE', 'OWNER')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, principal_type, principal_id, permission)
);
CREATE INDEX document_acl_lookup_idx ON document_acl(tenant_id, principal_type, principal_id);

CREATE TABLE chunks (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  document_version integer NOT NULL DEFAULT 1,
  chunk_index     integer NOT NULL,
  content         text NOT NULL,
  content_hash    text NOT NULL,
  token_count     integer NOT NULL DEFAULT 0,
  section_path    text NOT NULL DEFAULT '',
  page_from       integer,
  page_to         integer,
  classification  data_classification NOT NULL DEFAULT 'INTERNAL',
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Denormalised ACL principals, maintained from document_acl so that
  -- retrieval filters on access *before* ranking rather than after.
  acl_principals  text[] NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, document_version, chunk_index)
);
CREATE INDEX chunks_tenant_idx ON chunks(tenant_id);
CREATE INDEX chunks_acl_idx ON chunks USING gin (acl_principals);
CREATE INDEX chunks_fts_idx ON chunks USING gin (to_tsvector('english', content));

CREATE TABLE embeddings (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  chunk_id      uuid NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  model_key     text NOT NULL,
  dimensions    integer NOT NULL,
  embedding     vector(384) NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (chunk_id, model_key)
);
CREATE INDEX embeddings_vector_idx ON embeddings
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX embeddings_tenant_idx ON embeddings(tenant_id);

CREATE TABLE retrieval_queries (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id          uuid REFERENCES runs(id) ON DELETE SET NULL,
  user_id         uuid REFERENCES users(id),
  agent_key       text NOT NULL DEFAULT '',
  query_text      text NOT NULL,
  strategy        text NOT NULL DEFAULT 'hybrid',
  filters         jsonb NOT NULL DEFAULT '{}'::jsonb,
  candidates_before_acl integer NOT NULL DEFAULT 0,
  candidates_after_acl  integer NOT NULL DEFAULT 0,
  returned_count  integer NOT NULL DEFAULT 0,
  latency_ms      integer NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE citations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id        uuid REFERENCES runs(id) ON DELETE CASCADE,
  run_step_id   uuid REFERENCES run_steps(id) ON DELETE CASCADE,
  chunk_id      uuid REFERENCES chunks(id) ON DELETE SET NULL,
  document_id   uuid REFERENCES documents(id) ON DELETE SET NULL,
  quoted_text   text NOT NULL DEFAULT '',
  relevance     numeric(5,4),
  verified      boolean NOT NULL DEFAULT false,
  verification_method text NOT NULL DEFAULT 'SUBSTRING',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX citations_run_idx ON citations(run_id);

-- ---------------------------------------------------------------------------
-- Memory
-- ---------------------------------------------------------------------------
CREATE TABLE memory_records (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  memory_type   text NOT NULL CHECK (memory_type IN
                ('WORKING', 'EPISODIC', 'SEMANTIC', 'INSTITUTIONAL')),
  subject_type  text NOT NULL DEFAULT 'USER',
  subject_id    text NOT NULL DEFAULT '',
  run_id        uuid REFERENCES runs(id) ON DELETE CASCADE,
  content       text NOT NULL,
  content_hash  text NOT NULL,
  classification data_classification NOT NULL DEFAULT 'INTERNAL',
  provenance    jsonb NOT NULL DEFAULT '{}'::jsonb,
  owner_user_id uuid REFERENCES users(id),
  evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
  approved_by   uuid REFERENCES users(id),
  approved_at   timestamptz,
  confidence    numeric(5,4),
  expires_at    timestamptz,
  review_due_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX memory_lookup_idx ON memory_records(tenant_id, memory_type, subject_id);
-- Institutional memory is only valid with provenance, owner, evidence and approval.
ALTER TABLE memory_records ADD CONSTRAINT institutional_memory_requires_governance
  CHECK (
    memory_type <> 'INSTITUTIONAL'
    OR (approved_by IS NOT NULL AND owner_user_id IS NOT NULL
        AND jsonb_array_length(evidence_refs) > 0 AND review_due_at IS NOT NULL)
  );

-- ---------------------------------------------------------------------------
-- G-Brain: enterprise intelligence graph
-- ---------------------------------------------------------------------------
CREATE TABLE knowledge_nodes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  node_key      text NOT NULL,
  node_type     text NOT NULL CHECK (node_type IN
                ('PERSON', 'TEAM', 'AGENT', 'PROCESS', 'APPLICATION', 'DOCUMENT',
                 'ASSET', 'CUSTOMER', 'CONTRACT', 'PROJECT', 'KPI', 'RISK',
                 'DECISION', 'EVENT', 'CONTROL')),
  label         text NOT NULL,
  properties    jsonb NOT NULL DEFAULT '{}'::jsonb,
  classification data_classification NOT NULL DEFAULT 'INTERNAL',
  source_ref    text NOT NULL DEFAULT '',
  document_id   uuid REFERENCES documents(id) ON DELETE SET NULL,
  confidence    numeric(5,4) NOT NULL DEFAULT 1.0,
  valid_from    timestamptz NOT NULL DEFAULT now(),
  valid_to      timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, node_key)
);
CREATE INDEX knowledge_nodes_type_idx ON knowledge_nodes(tenant_id, node_type);
CREATE INDEX knowledge_nodes_label_trgm_idx ON knowledge_nodes USING gin (label gin_trgm_ops);

CREATE TABLE knowledge_edges (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  from_node_id  uuid NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
  to_node_id    uuid NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
  relation      text NOT NULL CHECK (relation IN
                ('owns', 'depends_on', 'created', 'approved', 'affects', 'uses',
                 'reports_to', 'related_to', 'caused_by', 'mitigates', 'measures',
                 'contains', 'derived_from')),
  properties    jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence    numeric(5,4) NOT NULL DEFAULT 1.0,
  source_ref    text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (from_node_id, to_node_id, relation)
);
CREATE INDEX knowledge_edges_from_idx ON knowledge_edges(tenant_id, from_node_id);
CREATE INDEX knowledge_edges_to_idx ON knowledge_edges(tenant_id, to_node_id);
ALTER TABLE knowledge_edges ADD CONSTRAINT knowledge_edges_no_self_loop
  CHECK (from_node_id <> to_node_id);
