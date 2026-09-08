# DailyOps — Agentic Business Operations Automation

## Problem

Một agent tự động hóa vận hành không chỉ đọc và báo cáo, mà có khả năng hành động thật ra bên ngoài hệ thống (gửi thông báo, tạo ticket, thực thi tác vụ). Rủi ro lớn nhất của loại agent này không phải "AI trả lời sai", mà là "AI tự ý thực thi hành động sai, không ai kiểm soát được, và không ai biết nó đã xảy ra". Bài toán kỹ thuật cốt lõi: làm sao để một LLM có quyền đề xuất hành động, nhưng không bao giờ có quyền tự cấp phép cho chính hành động đó.

## Architecture (tóm tắt)

`supervisor.py` điều phối các specialist agent (Business Analyst, Knowledge Agent, Operations Agent, Report Agent) theo từng loại tác vụ.
Mọi hành động đi qua `policy_engine.py`, được phân loại rủi ro: READ / LOW-RISK_WRITE / HIGH-RISK_WRITE / EXTERNAL_SIDE_EFFECT — mặc định fail-safe luôn chọn mức rủi ro cao nhất khi không chắc chắn.
Hành động EXECUTE không bao giờ được tự động duyệt — phải qua `approval_gate.py` (có TTL).
Sau khi hành động chạy, `guardrails/verifier.py` xác minh trạng thái thật bên ngoài, không chỉ tin HTTP 200 trả về.
Hàng đợi RabbitMQ (DLQ + ACK) xử lý task; tích hợp n8n (~60 node) và Postgres.

### Luồng Request → policy_engine → approval_gate → execute → verifier

```text
[Trigger: Telegram / Business Event / Schedule 08:00]
        │
        ▼
[n8n] Normalize Event ──► POST /agent/decide (Supervisor)
                                │
                                ▼
                ┌─── agent-service (FastAPI) ───┐
                │  supervisor.py                 │
                │   ├─ classify_operation()      │
                │   ├─ plan_delegation()         │
                │   │    → specialists (asyncio.gather)
                │   │      ├─ Business Analyst (sales/inventory)
                │   │      ├─ Knowledge Agent (RAG policy)
                │   │      ├─ Operations Agent (action proposals)
                │   │      └─ Report Agent
                │   └─ aggregate() evidence + confidence
                │                                │
                │  LLM output ──► assert_no_authorization_claims()
                │                 (strip injected authorized/approved/needs_approval)
                │                                │
                │  ▼ policy_engine.py            │
                │  (deterministic gate, isolated │
                │   from LLM — never reads any  │
                │   claim injected by model)     │
                │                                │
                │   ┌───────────┬───────────────┐│
                │   ALLOW      APPROVAL       DENY│
                │   ├─ READ     ├─ EXECUTE      │├─ destructive verbs
                │   ├─ LOW-RISK │  (all cases)  ││  (delete/drop/
                │   │  WRITE    ├─ restock>500  ││   truncate/shutdown)
                │   ├─ HIGH-    ├─ refund>200   │├─ malformed/non-str
                │   │  RISK     └─ pricing/CRM/ ││  recommended_action
                │   │  (above  │   bulk/export/ ││
                │   │  thresh) │   marketing/HR ││
                │   └───────────┴───────────────┘│
                │                                │
                │  ▼ approval_gate.py            │
                │  ├─ literal "approve"/"reject" │
                │  │  only (DAILYOPS-03)         │
                │  ├─ TTL → auto-ESCALATED      │
                │  │  (DAILYOPS-04)              │
                │  └─ idempotency reserve       │
                │     (INSERT ON CONFLICT DO    │
                │      NOTHING — DAILYOPS-05)   │
                │                                │
                │  APPROVED ──► execute ──►      │
                │               verifier.py       │
                │               (verify external  │
                │                state, not just  │
                │                HTTP 200)        │
                └────────────────────────────────┘
                                │
                                ▼
[n8n 60 nodes] Route by action_type → Execute Target API → Verify → Record Outcome (Postgres)
```

**Nguyên tắc cốt lõi:** LLM (RAG + Cloudflare Workers AI) và Policy Engine nằm 100% trong `agent-service`. n8n không nghĩ — chỉ orchestrate (fetch → decide → route → execute → verify).

---

## Vulnerability found & fixed — LLM tự nhận mình đã được duyệt

**Phát hiện:** output của chính LLM có thể chứa các trường như `authorized`, `approved`, `needs_approval`, `policy_verdict` — kể cả khi bị lồng trong object con (nested dict/list) — và nếu hệ thống tin vào đó, LLM coi như tự cấp quyền cho chính mình.

**Fix:**
- `assert_no_authorization_claims()` trong `policy_engine.py` strip mọi claim key ở top-level khỏi output LLM trước khi bất kỳ policy input nào được tính — không có ngoại lệ.
- Supervisor chỉ đọc 3 key canonical (`action_type`, `recommended_action`, `ticket_type`) nên nested claims về cấu trúc là **không bao giờ đọc được**.
- `check()` swallow mọi extra kwarg (`**_ignored_authorization_claims`) — phòng tương lai nếu ai đó thêm logic đọc claim.
- **Edge case cũng đã khóa:** `recommended_action` không phải string (dict/list từ LLM bị poison) → fail-safe vào APPROVAL, không silent auto-allow qua numeric threshold (lỗi `str()` coercion fail-open đã được phát hiện và sửa).

**Nguồn quyết định thật duy nhất:** `policy_engine.py` (deterministic) + con người qua `approval_gate.py`.

**Vì sao quan trọng:** đây là lớp phòng thủ chống lại chính hành vi "prompt injection ngược" — mô hình tự thao túng luồng quyết định của hệ thống nó đang chạy trong đó.

---

## Test coverage / metrics

| Metric | Value |
|--------|-------|
| Tổng test cases | **112** (13 file, `pytest tests/ -q`, 0 failure) |
| Source LOC (agent-service/src) | **2,553** |
| n8n workflow nodes | **60** |
| Policy verdict assertions trong test | **5 DENY / 15 APPROVAL / 10 ALLOW** |
| Failure-class assertions | **10 APPROVAL_REQUIRED / 6 FAILED** |
| Risk groups (policy_engine) | READ / LOW-RISK_WRITE / HIGH-RISK_WRITE / EXTERNAL_SIDE_EFFECT |
| Policy engine basis | Deterministic rule-based — không đọc field nào từ LLM output ngoài 3 key canonical |
| Fail-safe mặc định | Unknown action → EXTERNAL_SIDE_EFFECT (highest risk); malformed input → APPROVAL |
| Parallel speedup (bench_parallel.py) | ~3.1× với 3 specialists (asyncio.gather vs sequential) |

**Bảo vệ đã verify qua test:**
- LLM claim injection (top-level + nested) → bỏ qua, gate vẫn đóng.
- Destructive verbs → DENY trên mọi action_type.
- EXECUTE → luôn APPROVAL (không auto-execute).
- Idempotency: 5 replay cùng key → 1 action thật (unique constraint ở DB).
- Approval TTL → PENDING quá hạn tự ESCALATED.
- Retry safety: UNKNOWN_OUTCOME (timeout) → reconciliation trước khi retry; không blind-retry side-effect.
- Verification: HTTP 200 nhưng business fail (contract breach) → bị verifier phát hiện.

---

## What I'd do next

**G-06 — Circuit breaker cho external target systems.**

Hiện tại hệ thống có n8n retry (maxTries: 3, waitBetweenTries: 2s) và Prometheus dead-letter metric (`dailyops_deadletter_total` trong Grafana dashboard), nhưng **chưa có circuit breaker thực sự** ở tầng Python. Khi một external API (ERP/CRM/Sheets) down, hệ thống vẫn gọi liên tục theo retry và đẩy vào DLQ — tốn resource và che mặt failure gốc.

Bước tiếp theo:
- Thêm circuit breaker per target system (phủ lên `Execute Target API`): sau N consecutive failures → mở mạch, fast-fail ngay lập tức với alert thay vì retry vô hạn. Tích hợp với metric `dailyops_deadletter_total` đã có trong monitoring dashboard để visualize breaker state (closed / open / half-open).
- Đây là natural extension của DAILYOPS-08 (retry safety + UNKNOWN_OUTCOME) và G-06 (Reliability Controls) — biến retry mù thành fail-fast có kiểm soát.


---


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
supervisor_latency_ms, idempotency_key`.
Bảng auto/approval + thresholds sống trong `agent-service/src/guardrails/policies.yaml` — không hard-code trong prompt hay n8n node.

## Auth & idempotency (hardening)

- **Auth**: đặt `AGENT_API_KEY` trong `.env` → mọi request `/agent/*` phải mang header
  `X-API-Key` (n8n đọc `$env.AGENT_API_KEY` tự động qua các node HTTP). Bỏ trống = open
  mode (chỉ dùng dev). `/health` luôn mở cho healthcheck.
- **Webhook auth (DAILYOPS-06)**: set `WEBHOOK_SECRET` → webhook `Business Event` bắt buộc
  header `X-Webhook-Secret`; approval link cho human được ký HMAC-SHA256 (token) — link
  sai/thiếu token bị reject trước khi chạm approvals. Bỏ trống = open dev mode.
- **Approval (DAILYOPS-03/04)**: chỉ literal `approve`/`reject` mới đổi trạng thái approvals
  (mọi biến thể khác — `approved`, `APPROVED BUT...`, `true` — bị bỏ qua, stays PENDING);
  PENDING quá TTL (`APPROVAL_TTL_HOURS`, mặc định 24h) → tự ESCALATED + escalate human,
  không bao giờ execute sau TTL. Áp dụng migration:
  `docker exec -i dailyops-postgres psql -U dailyops -d dailyops < db/migrations/005_approval_ttl.sql`.
- **Idempotency (DAILYOPS-05)**: decision trả về `idempotency_key` (hash của correlation_id + action).
  n8n ghi 1 row `action_executions` (`INSERT ... ON CONFLICT DO NOTHING`) **trước** khi
  Execute Target API **và** trước lane NOTIFY — retry/webhook replay không double-execute
  (email/ticket/order chỉ xảy ra 1 lần mỗi `idempotency_key`, unique constraint ở DB).
- **Retry safety (DAILYOPS-08)**: failure được phân loại `UNKNOWN_OUTCOME` (timeout —
  side-effect có thể đã xảy ra) vs `FAILED`. Không bao giờ blind-retry side-effect;
  `UNKNOWN_OUTCOME` phải qua reconciliation (theo `external_operation_id`) trước khi retry.

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
python3 -m pytest tests/ -q        # 170 tests: contract/orchestrator + auth + policy hardening + idempotency + destructive-word centralization
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
