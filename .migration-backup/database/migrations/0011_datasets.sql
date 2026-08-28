-- 0011 Governed structured datasets.
--
-- Enterprise agents mostly reason over tabular exports (work orders, asset
-- registers, invoices) rather than prose. Storing them as first-class governed
-- datasets — with row-level lineage back to the source file and batch — lets
-- tools query them deterministically instead of asking a model to read a CSV.

CREATE TABLE datasets (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  dataset_key     text NOT NULL CHECK (dataset_key ~ '^[a-z][a-z0-9_.-]{2,63}$'),
  name            text NOT NULL,
  description     text NOT NULL DEFAULT '',
  source_system   text NOT NULL DEFAULT 'upload',
  owner_team      text NOT NULL DEFAULT '',
  classification  data_classification NOT NULL DEFAULT 'INTERNAL',
  schema_fields   jsonb NOT NULL DEFAULT '[]'::jsonb,
  primary_key_field text NOT NULL DEFAULT '',
  row_count       integer NOT NULL DEFAULT 0,
  quality_score   numeric(5,4),
  quality_detail  jsonb NOT NULL DEFAULT '{}'::jsonb,
  freshness_at    timestamptz,
  status          lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, dataset_key)
);

CREATE TABLE dataset_batches (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  dataset_id      uuid NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  document_id     uuid REFERENCES documents(id) ON DELETE SET NULL,
  source_file     text NOT NULL DEFAULT '',
  source_hash     text NOT NULL DEFAULT '',
  row_count       integer NOT NULL DEFAULT 0,
  rejected_count  integer NOT NULL DEFAULT 0,
  ingested_by     uuid REFERENCES users(id),
  ingested_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dataset_rows (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  dataset_id      uuid NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  batch_id        uuid NOT NULL REFERENCES dataset_batches(id) ON DELETE CASCADE,
  row_key         text NOT NULL DEFAULT '',
  source_row_no   integer NOT NULL DEFAULT 0,
  data            jsonb NOT NULL,
  quality_flags   text[] NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dataset_rows_dataset_idx ON dataset_rows(tenant_id, dataset_id);
CREATE INDEX dataset_rows_key_idx ON dataset_rows(dataset_id, row_key);
CREATE INDEX dataset_rows_data_idx ON dataset_rows USING gin (data jsonb_path_ops);

-- Apply the standard isolation policy to the new tables.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['datasets', 'dataset_batches', 'dataset_rows'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I
         USING (tenant_id = app_current_tenant())
         WITH CHECK (tenant_id = app_current_tenant())', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON datasets, dataset_batches, dataset_rows TO agentic_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON datasets, dataset_batches, dataset_rows TO agentic_provisioner;
