-- 004_idempotency: chống double-execute khi n8n retry / webhook replay.
-- agent-service trả về `idempotency_key` trong Decision; n8n ghi 1 row trước
-- khi Execute Target API: INSERT ... ON CONFLICT (idempotency_key) DO NOTHING.
-- Áp dụng: docker exec -i dailyops-postgres psql -U dailyops -d dailyops < db/migrations/004_idempotency.sql
CREATE TABLE IF NOT EXISTS action_executions (
  id SERIAL PRIMARY KEY,
  idempotency_key TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  target_system TEXT NOT NULL DEFAULT '',
  executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_action_executions_idem ON action_executions (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_action_executions_correlation ON action_executions (correlation_id);
