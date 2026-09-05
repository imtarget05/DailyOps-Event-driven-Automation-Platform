-- 002_outcomes: closed-loop evaluation. Ghi outcome của decision/action
-- (không phụ thuộc notification có gửi được hay không).
-- Áp dụng: docker exec -i dailyops-postgres psql -U dailyops -d dailyops < db/migrations/002_outcomes.sql
CREATE TABLE IF NOT EXISTS decision_outcomes (
  id SERIAL PRIMARY KEY,
  correlation_id TEXT NOT NULL UNIQUE,
  action_type TEXT NOT NULL,
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_correlation ON decision_outcomes (correlation_id);
