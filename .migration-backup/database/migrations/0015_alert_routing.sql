-- 0015 Give alerts somewhere to go.
--
-- The `alerts` table has existed since 0006 and has never held a row. Nothing
-- in the platform raised one: the only statement touching it anywhere was the
-- SELECT behind the operations surface. A table that is only ever read is a
-- decorative control, and it is the one the readiness report was describing
-- when it said observability here "would support an investigation and will not
-- surface a problem to a human unprompted".
--
-- Acknowledgement columns were already present and equally unused. What was
-- genuinely missing is everything that makes an alert reach somebody:
--
--  * a status, so an alert can be open, acknowledged or resolved rather than
--    merely existing;
--  * an assignee and the domain that scopes it, so routing works the way
--    notifications already do — permission *and* domain membership, never one
--    alone;
--  * a deduplication key, so a condition that stays true for six hours
--    produces one alert rather than seventy-two;
--  * escalation, so an unacknowledged CRITICAL does not sit unread forever.
--
-- Deduplication is the part worth being careful about. Without it an alerting
-- pass becomes a generator of noise, and an operator who learns to ignore the
-- alert list is worse off than one who never had it.

ALTER TABLE alerts
  ADD COLUMN status text NOT NULL DEFAULT 'OPEN'
    CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'SUPPRESSED')),
  ADD COLUMN dedupe_key text NOT NULL DEFAULT '',
  ADD COLUMN domain_id uuid REFERENCES domains(id) ON DELETE SET NULL,
  ADD COLUMN assigned_to_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN assigned_at timestamptz,
  ADD COLUMN required_permission text NOT NULL DEFAULT '',
  ADD COLUMN escalated_at timestamptz,
  ADD COLUMN escalation_level integer NOT NULL DEFAULT 0
    CHECK (escalation_level >= 0),
  ADD COLUMN resolved_at timestamptz,
  ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN occurrence_count integer NOT NULL DEFAULT 1
    CHECK (occurrence_count > 0),
  -- An acknowledgement is a claim that a person looked. It must name them.
  ADD CONSTRAINT alert_acknowledgement_names_someone CHECK (
    (acknowledged_at IS NULL AND acknowledged_by IS NULL)
    OR (acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL)
  ),
  -- A resolved alert that is still OPEN, or an open one stamped resolved,
  -- would make every count on the surface wrong.
  ADD CONSTRAINT alert_resolution_matches_status CHECK (
    (status = 'RESOLVED') = (resolved_at IS NOT NULL)
  );

-- One live alert per condition. The partial predicate is what makes
-- deduplication work: a resolved alert does not block the same condition
-- recurring later, which would silently swallow the second outage.
CREATE UNIQUE INDEX alerts_live_dedupe_idx
  ON alerts(tenant_id, dedupe_key)
  WHERE status <> 'RESOLVED' AND dedupe_key <> '';

-- 0006's alerts_open_idx keyed "open" off acknowledged_at IS NULL, which now
-- misses an alert acknowledged and then reopened. Replaced by one that reads
-- the status column this migration introduces.
DROP INDEX IF EXISTS alerts_open_idx;
CREATE INDEX alerts_open_idx ON alerts(tenant_id, status, severity, created_at DESC);
CREATE INDEX alerts_assignee_idx ON alerts(assigned_to_user_id);
CREATE INDEX alerts_domain_idx ON alerts(domain_id);
CREATE INDEX alerts_incident_idx ON alerts(incident_id);
CREATE INDEX alerts_acknowledged_by_idx ON alerts(acknowledged_by);
