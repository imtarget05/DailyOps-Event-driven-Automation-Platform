# DailyOps QA — Test Matrix & Adjudications (Phases 3–7) + Phase 9 outlook

> **Date**: 2026-09-08  ·  **Branch**: feat/multi-agent-orchestrator
> Grounded in the Phase 2 baseline report (`qa-baseline-report.md`). Each row is
> adjudicated **PASS** (verified now) or **BLOCKED** (external dependency missing —
> never reported as PASS). Phase boundaries follow the QA plan's dimensions; where a
> phase maps to an external thread, the case is enumerated so a full-environment run can
> execute it as-is.

## Adjudication legend
- **PASS** — exercised end-to-end in this environment (live HTTP and/or 114-test suite + pyright).
- **BLOCKED** — dependency not available here (real LLM creds, n8n runtime, RabbitMQ, DB writer). Criterion + expected outcome are specified so it is ready to run where deps exist.

## Matrix

| Phase | Dimension | Case | Criterion | Adjudication | Evidence |
|---|---|---|---|---|---|
| 3 — Classification | `classify_operation` event→op mapping | daily_report→daily_report | action_type REPORT | **PASS** | case B |
| 3 — Classification | telegram_chat→inventory_analysis | "kho ...hết hàng" → TICKET | `operation_type=inventory_analysis` | **PASS** | suite case 8 |
| 3 — Classification | refund_request→approval_action | callback EXECUTE+APPROVAL | `policy_matched=finance_refund_approval` | **PASS** | case D / suite case 4 |
| 3 — Classification | destructive intent | force EXECUTE→DENY | fail-closed, no side effect | **PASS** | case E / suite case 5 |
| 3 — Classification | smalltalk fast-path | "hi" → NOTIFY, no specialists | `operation_type=smalltalk`, zero specialists | **PASS** | live P3a |
| 3 — Classification | advisory_request | council REPORT only | `op=advisory_request`, `specs=[knowledge,advisory]`, `policy_matched=advisory_report_only` | **PASS** | live P3b |
| 3 — Classification | general_request fallback | business_event → analyst+knowledge | `op=general_request` | **PASS** | live P3c |
| 4 — Delegation | specialist plan per op | inventory→analyst+knowledge+ops | 3 specialists, 8 evidence items | **PASS** | case C |
| 4 — Delegation | concurrency | parallel < sequential wall-time | t_par < t_seq | **PASS** | suite case 7 |
| 4 — Delegation | report 2nd-wave enrichment | daily_report→report_agent summaries | `report_agent in specialists_used` | **PASS** | case B / suite case 1 |
| 4 — Reliability | specialist crash isolation | boom→failed, sibling completed | no wave abort | **PASS** | suite case 7b |
| 4 — Reliability | specialist timeout | hang→failed after budget, sibling ok | degraded AgentResult | **PASS** | suite case 7c |
| 5 — Guardrails | typed gate (PolicyRequest) | check_request ≡ check | same verdict | **PASS** | suite policy-independence |
| 5 — Guardrails | claim-strip independence | poisoned LLM → gate overrules | APPROVAL not auto-ALLOW | **PASS** | suite policy-independence |
| 5 — Guardrails | risk taxonomy | all 4 groups populated | no unclassified tool | **PASS** | suite policy-independence |
| 5 — Guardrails | destructive denylist | token-boundary denial | DENY | **PASS** | case E |
| 6 — API/auth | auth open-mode (dev) | no key → 200 | dev smoke path | **PASS** | cases A–F |
| 6 — API/auth | auth enforced | no/wrong key → 401; correct key → 200; /health stays open 200 | `AGENT_API_KEY` honor | **PASS** | live 8081 (no-key 401, wrong-key 401, correct 200, /health 200) |
| 6 — API/DB | decisions/action_executions rows | unique collapse on idempotency_key | 1 row per key | **BLOCKED** (writer = n8n, not running) | DB healthy, 0 rows |
| 7 — External effect | Sheets/Gmail/Telegram/Ticket | n8n executes proposed action | side effect occurs | **BLOCKED** (n8n down) | proposals verified only |
| 7 — External effect | RabbitMQ threads | event publish/consume | message flow | **BLOCKED** (component absent) | — |
| 7 — External LLM | Cloudflare reasoner | real grounding/interpretation | LLM output + auth-claim classification | **BLOCKED** (no creds) | mock used |

## Phase 9 — QA report (outlook)

Draft sections (to finalize after Phases 3–7 run in a full environment):
1. **Summary** — exporter-visible PASS vs BLOCKED counts.
2. **Guardrail certification** — policy gate behavior (DENY/APPROVAL/ALLOW matrix) with verdicts.
3. **Reliability** — crash/timeout isolation + parallel speedup measurements.
4. **Idempotency** — DB unique-collapse rows classified by idempotency_key.
5. **Residual risk register** (all currently BLOCKED threads) — Real LLM, n8n side-effects, RabbitMQ,
   DB-row writes.

Current expectation: 5 BLOCKED rows above resolve to runnable after secrets are set and
`make up` brings up n8n + RabbitMQ; agent-service-side rows are already green.