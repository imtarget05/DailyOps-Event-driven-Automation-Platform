-- 001_approvals: chạy 1 lần cho DB đã tồn tại (schema.sql chỉ chạy khi init mới).
-- Áp dụng: docker exec -i dailyops-postgres psql -U dailyops -d dailyops < db/migrations/001_approvals.sql
CREATE TABLE IF NOT EXISTS approvals (
  id SERIAL PRIMARY KEY,
  correlation_id TEXT NOT NULL UNIQUE,
  requested_action TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING','APPROVED','REJECTED')),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_by TEXT NOT NULL DEFAULT '',
  decided_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approvals_correlation ON approvals (correlation_id);
