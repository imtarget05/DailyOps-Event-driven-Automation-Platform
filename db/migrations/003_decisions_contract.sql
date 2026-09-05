-- 003_decisions_contract: thống nhất contract Record Decision (10 fields).
-- decision_id sinh SQL-side ('dec_' + random) để không đổi contract agent-service.
-- Áp dụng: docker exec -i dailyops-postgres psql -U dailyops -d dailyops < db/migrations/003_decisions_contract.sql
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS decision_id TEXT;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Backfill decision_id cho rows cũ (nếu có) để giữ nhất quán audit.
UPDATE decisions SET decision_id = 'dec_' || substr(md5(correlation_id), 1, 8)
WHERE decision_id IS NULL;
