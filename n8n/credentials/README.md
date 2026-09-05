# Credentials checklist — quy trình: node nào chưa xong credential thì workflow chưa "done"

Đánh dấu [x] khi đã cấu hình trong n8n UI (http://localhost:5678 → Credentials).
Quy tắc merge canvas: **không còn node warning credential mới được coi là xong.**

## Bắt buộc cho luồng chính chạy local

- [x] **DailyOps Postgres** (7 node: Record Decision, Create/Close Ticket, Request/Record/Load Approval, Record Outcome)
  - Auto-provisioned: `n8n/credentials/postgres.json` → `docker compose run --rm -v ./n8n/credentials:/creds:ro n8n import:credentials --input=/creds/postgres.json`
  - Host `postgres:5432`, db/user/pass `dailyops` (dev default theo `.env`; rotate khi lên prod)
- [ ] **Cloudflare Workers AI** (không phải node credential — điền `.env` rồi `docker compose up -d n8n`)
  - `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_MODEL`
  - Chưa điền → agent-service dùng mock fallback (vẫn chạy, đã có test)
- [ ] **Telegram bot** (2 node: Telegram Chat In, Reply Telegram — cùng 1 bot)
  - Token từ BotFather; local cần tunnel + `WEBHOOK_URL` để Telegram gọi được webhook

## Bắt buộc trước go-live (hiện để placeholder)

- [ ] **Gmail OAuth2** (node Send Gmail) + sửa người nhận `ops-team@example.com`
- [ ] **Google Sheets OAuth2** (node Append Google Sheet) + thay `REPLACE_WITH_SHEET_ID`
- [ ] **Execute Target API**: thay `https://example.com/api/execute` bằng ERP/API thật
- [ ] **Notify Approver**: thay `http://localhost:5678` bằng `WEBHOOK_URL` production
