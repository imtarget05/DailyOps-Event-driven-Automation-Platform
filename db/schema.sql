-- DailyOps v2 minimal schema: Postgres is the single source of truth.
-- Every table carries correlation_id for end-to-end tracing:
-- trigger -> decision -> action -> verify -> close.

CREATE TABLE IF NOT EXISTS workflow_runs (
  id SERIAL PRIMARY KEY,
  correlation_id TEXT NOT NULL UNIQUE,
  workflow_name TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'RUNNING'
);

CREATE TABLE IF NOT EXISTS decisions (
  id SERIAL PRIMARY KEY,
  decision_id TEXT NOT NULL DEFAULT '',
  correlation_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  entity TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  action_type TEXT NOT NULL CHECK (action_type IN ('REPORT','NOTIFY','TICKET','EXECUTE')),
  needs_approval BOOLEAN NOT NULL DEFAULT FALSE,
  policy_matched TEXT NOT NULL DEFAULT '',
  raw_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tickets (
  id SERIAL PRIMARY KEY,
  ticket_id TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'medium',
  entity TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'OPEN'
    CHECK (status IN ('OPEN','IN_PROGRESS','ACTION','VERIFY','RESOLVED','CLOSED')),
  recommendation TEXT NOT NULL DEFAULT '',
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  correlation_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_correlation ON tickets (correlation_id);
CREATE INDEX IF NOT EXISTS idx_decisions_correlation ON decisions (correlation_id);
CREATE INDEX IF NOT EXISTS idx_runs_correlation ON workflow_runs (correlation_id);

CREATE TABLE IF NOT EXISTS actions (
  id SERIAL PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  action_type TEXT NOT NULL CHECK (action_type IN ('REPORT','NOTIFY','TICKET','EXECUTE')),
  target_system TEXT NOT NULL DEFAULT '',
  payload JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING','SUCCESS','FAILED')),
  attempt_count INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_actions_correlation ON actions (correlation_id);

-- Approval loop: EXECUTE cần duyệt dừng ở PENDING; human bấm link approve/reject
-- (Approval Decision webhook) -> UPDATE status -> APPROVED mới được Execute.
-- DAILYOPS-03: chỉ accept literal 'approve'/'reject'; DAILYOPS-04: TTL qua expires_at.
CREATE TABLE IF NOT EXISTS approvals (
  id SERIAL PRIMARY KEY,
  correlation_id TEXT NOT NULL UNIQUE,
  requested_action TEXT NOT NULL DEFAULT '',
  idempotency_key TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED','ESCALATED')),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  decided_by TEXT NOT NULL DEFAULT '',
  decided_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approvals_correlation ON approvals (correlation_id);

CREATE TABLE IF NOT EXISTS decision_outcomes (
  id SERIAL PRIMARY KEY,
  correlation_id TEXT NOT NULL UNIQUE,
  action_type TEXT NOT NULL,
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_correlation ON decision_outcomes (correlation_id);

-- Idempotency: một (correlation_id, action_type, recommended_action) chỉ ghi 1 row.
-- n8n retry / webhook replay sẽ ON CONFLICT DO NOTHING thay vì double-execute.
CREATE TABLE IF NOT EXISTS action_executions (
  id SERIAL PRIMARY KEY,
  idempotency_key TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  target_system TEXT NOT NULL DEFAULT '',
  outcome_kind TEXT NOT NULL DEFAULT 'SUCCESS',
  external_operation_id TEXT NOT NULL DEFAULT '',
  reconciliation_needed BOOLEAN NOT NULL DEFAULT FALSE,
  executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_action_executions_idem ON action_executions (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_action_executions_correlation ON action_executions (correlation_id);
