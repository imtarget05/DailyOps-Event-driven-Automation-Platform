-- 005_approval_ttl + outcome columns: DAILYOPS-04/05/08.
-- - approvals: TTL (expires_at), idempotency_key (resume path dedup), ESCALATED/CANCELLED states.
-- - action_executions: phan biet SUCCESS/FAILED/UNKNOWN_OUTCOME + reconciliation (khong blind-retry side-effect).
-- Áp dụng: docker exec -i dailyops-postgres psql -U dailyops -d dailyops < db/migrations/005_approval_ttl.sql

ALTER TABLE approvals ADD COLUMN IF NOT EXISTS idempotency_key TEXT NOT NULL DEFAULT '';
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
UPDATE approvals SET expires_at = requested_at + interval '24 hours' WHERE expires_at IS NULL;

ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_status_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_status_check
  CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED','ESCALATED'));

ALTER TABLE action_executions ADD COLUMN IF NOT EXISTS outcome_kind TEXT NOT NULL DEFAULT 'SUCCESS';
ALTER TABLE action_executions ADD COLUMN IF NOT EXISTS external_operation_id TEXT NOT NULL DEFAULT '';
ALTER TABLE action_executions ADD COLUMN IF NOT EXISTS reconciliation_needed BOOLEAN NOT NULL DEFAULT FALSE;

-- ROLLBACK (DAILYOPS-OPS-1) — chỉ chạy nếu buộc phải revert; mất cột dữ liệu TTL/outcome:
--   ALTER TABLE approvals DROP COLUMN IF EXISTS idempotency_key;
--   ALTER TABLE approvals DROP COLUMN IF EXISTS expires_at;
--   ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_status_check;
--   ALTER TABLE approvals ADD CONSTRAINT approvals_status_check
--     CHECK (status IN ('PENDING','APPROVED','REJECTED'));
--   ALTER TABLE action_executions DROP COLUMN IF EXISTS outcome_kind;
--   ALTER TABLE action_executions DROP COLUMN IF EXISTS external_operation_id;
--   ALTER TABLE action_executions DROP COLUMN IF EXISTS reconciliation_needed;
-- Lưu ý: rollback FAILED nếu còn approval ở trạng thái CANCELLED/ESCALATED
-- (vi phạm CHECK cũ) — chuyển chúng về REJECTED trước khi rollback.
-- Migration này idempotent (IF NOT EXISTS) — chạy lại an toàn, không mất dữ liệu.