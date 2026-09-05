# DailyOps — Event-driven Automation Platform (v2)

> **n8n chỉ orchestrate; agent-service chỉ reasoning và đề xuất; Postgres là nguồn sự thật duy nhất; mọi action ghi đều phải qua Policy Engine và được verify.**

## Kiến trúc

```text
[n8n] Trigger → Normalize → POST /agent/decide (Supervisor) → Attach Context
      → Specialist team (4× POST /agent/specialist/*, parallel)
      → Join Specialists (Merge barrier: 1 execution downstream, no 4x actions)
      → Aggregate → Policy/Guardrail → Switch (REPORT | NOTIFY | TICKET | EXECUTE)
      → lane-local Verify → Outcome → Feedback/Eval → Postgres
```

```text
              DAILYOPS SUPERVISOR (POST /agent/decide)
                    "Cần chuyên môn nào?"
        ┌───────────────┼───────────────┐
        ↓               ↓               ↓
   BUSINESS         KNOWLEDGE       OPERATIONS
    ANALYST           AGENT            AGENT
   sales/          policy/RAG       action/
   inventory                         proposals
        └───────────────┼───────────────┘
                        ↓ (+ Report Agent)
                  AGGREGATOR
                        ↓
                   GUARDRAIL
                   (ALLOW | APPROVAL | DENY)
                        ↓
             REPORT / NOTIFY / TICKET / EXECUTE
                        ↓
                     VERIFY → OUTCOME → FEEDBACK
```

Ví dụ business (tồn kho): `inventory_check(product_A, stock=15, avg=8)` →
Supervisor gọi song song Business Analyst (stock_days ~1.9 → HIGH) +
Knowledge Agent (policy `restock_auto_threshold`) + Operations Agent
(đề xuất `create_restock_request qty=200`) → aggregate → Policy ALLOW →
`TICKET inventory_risk` → Create Ticket → Verify → Close → Record Outcome.

## Cấu trúc repo

```text
n8n/workflows/        orchestration (thin: fetch → call agent → route → verify)
agent-service/        microservice độc lập, 1 API chính POST /agent/decide
db/schema.sql         Postgres: workflow_runs, decisions, tickets, actions (tất cả có correlation_id)
monitoring/           prometheus + dashboards
```

## Chạy

```bash
cp .env.example .env
docker compose up -d --build
make decide-health   # smoke-test POST /agent/decide
```

## Contract

Request `POST /agent/decide` và response `Decision` xem trong `agent-service/src/schemas/models.py`.
Response giữ nguyên các field v2 (`correlation_id, decision, action_type, recommended_action,
needs_approval, policy_matched, ticket_payload`) và thêm trace supervisor:
`decision_id, operation_type, priority, specialists_used, evidence, action, failure_kind,
supervisor_latency_ms`.
Bảng auto/approval sống trong `agent-service/src/guardrails/policies.yaml` — không hard-code trong prompt hay n8n node.

Endpoints (agent-service):

| Endpoint | Mục đích |
|---|---|
| `POST /agent/decide` | Supervisor: classify → parallel specialists → aggregate → policy (hợp đồng chính với n8n) |
| `POST /agent/specialist/{business_analyst,knowledge_agent,operations_agent,report_agent}` | Gọi 1 specialist (cluster song song trên canvas n8n) |
| `POST /agent/aggregate` | Gộp các `AgentResult` thành decision (tham chiếu cho node Aggregate) |
| `GET /agent/tools` | Danh mục tools (READ/OUTPUT/ACTION + permission class) |
| `GET /agent/metrics` | Counters + latency theo agent/tool |
| `GET /health` | Healthcheck |

## Kiểm thử & benchmark

```bash
cd agent-service
python3 -m pytest tests/ -q        # 13 tests: 4 contract cũ + 9 orchestrator (delegation, parallel, policy, deny, failure)
python3 bench_parallel.py          # T_sequential vs T_parallel + parallel_speedup (đo được ~3.1x với 3 specialists)
node ../scripts/validate-workflows.js   # validate canvas n8n (51 nodes OK)
```

## Luồng Telegram + Cloudflare LLM (workflow duy nhất: `DailyOps — Unified`)

```text
Telegram Chat In / Business Event / Daily 08:00
  → Normalize → Decide POST /agent/decide (LỆNH GỌI DUY NHẤT, §4)
      → Attach Channel Context (giữ chat_id, plumbing thuần — không reasoning)
        → Route theo action_type
          REPORT  → Append Google Sheet → Record Report Run (workflow_runs, KHÔNG qua Close Ticket)
          NOTIFY  → Has Telegram Chat? → Reply Telegram → Send Gmail (fallback) → Record Notify Run
          TICKET  → Create Ticket → Verify → Close Ticket
          EXECUTE → Needs Approval?
                      ├─ false → Execute Target API → Verify → Close Ticket
                      └─ true  → Request Approval (approvals=PENDING) + Notify Approver (link)
                                  → human bấm link → Approval Decision webhook
                                  → UPDATE approvals → APPROVED mới Execute (kín vòng)
```

Nguyên tắc giữ vững: **LLM (RAG + Cloudflare Workers AI + parse) và Policy Engine nằm
100% trong `agent-service`. n8n không nghĩ** — node `Attach Channel Context` chỉ gộp
dữ liệu (DecideResponse + Event gốc), không chứa prompt/rule.

Chi tiết credential theo checklist merge canvas: `n8n/credentials/README.md`.
Postgres đã auto-provision (`DailyOps Postgres`); còn lại Telegram/Gmail/Sheets cần gắn tay trên UI.
Validator `scripts/validate-workflows.js` kiểm tra cấu trúc (fail khi hỏng) + audit credentials/placeholder
dưới dạng warning (không fail CI).

## Việc cần làm trên canvas n8n (http://localhost:5678)

| Node | Credential cần tạo |
|---|---|
| Telegram Chat In + Reply Telegram | Telegram bot (token từ BotFather) |
| Cloudflare LLM | Không cần node credential — điền `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` vào `.env` rồi `docker compose up -d n8n` |
| Send Gmail | Gmail OAuth2 + sửa người nhận (đang để `ops-team@example.com`) |
| Append Google Sheet | Google Sheets OAuth2 + thay `REPLACE_WITH_SHEET_ID` bằng Sheet ID thật |
| Create/Close/Request Ticket | Postgres (`postgres:5432`, db `dailyops`) |

Lưu ý: Telegram chỉ gọi được webhook qua public URL — khi chạy local, dùng tunnel
(`cloudflared`/`ngrok`) và set `WEBHOOK_URL` tương ứng trong `.env`.
