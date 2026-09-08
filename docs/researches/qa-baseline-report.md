# DailyOps QA — Baseline Report (Phase 2)

> **Date**: 2026-09-08
> **Branch**: feat/multi-agent-orchestrator
> **Scope**: Exercisable-baseline evidence for the multi-agent orchestrator. Confirms
> what actually ran and what is factually blocked in this environment.

## 1. Environment

| Component | State | Evidence |
|---|---|---|
| agent-service venv | READY | `.venv` present; fastapi/uvicorn/httpx/yaml importable |
| Postgres `dailyops-postgres` (postgres:16, :5435) | HEALTHY | 7 tables present (migrations 001–005 applied): `decisions`, `action_executions`, `approvals`, `decision_outcomes`, `tickets`, `actions`, `workflow_runs` |
| Postgres row counts at baseline | 0 across all tables | nothing had been written (write path is n8n-side) |
| Port 8000 | OCCUPIED by unrelated `hermes` service | agent-service started on 8080 instead |
| Agent-service uvicorn (127.0.0.1:8080) | RUNNING | daemonized (double-fork+setsid), survived session |

## 2. Live HTTP evidence

| # | Case | HTTP | Key assertions | Verdict |
|---|---|---|---|---|
| A | `GET /health` | 200 | `{"status":"ok"}` | PASS |
| B | `daily_report` → REPORT | 200 | `action_type=REPORT`, `policy_matched=report_auto`, 2 specialists, `failure_kind=SUCCESS`, `idempotency_key` present, `priority=LOW` | PASS |
| C | `inventory_check` (stock 15, avg 8) → TICKET | 200 | `action_type=TICKET`, `recommended_action=restock_200`, `policy_matched=inventory_policy.restock_auto_threshold`, `needs_approval=false`, knowledge-agent KB evidence cited, `priority=HIGH` | PASS |
| D | `refund_request` 1500 → EXECUTE/APPROVAL | 200 | `action_type=EXECUTE`, `recommended_action=refund_1500`, `needs_approval=true`, `policy_matched=finance_refund_approval`, `failure_kind=APPROVAL_REQUIRED` | PASS |
| E | destructive `telegram_chat` → DENY | 200 | `action_type=EXECUTE`, `recommended_action=delete_system_data`, `policy_matched=destructive_action_deny`, `failure_kind=DENIED` (fail-closed) | PASS |
| F | idempotency determinism | 200 | same request sent twice → identical `idempotency_key` (`071b3dbee98b19ac` both times) | PASS (agent layer) |

## 3. Automated cross-check

- `python -m pytest agent-service/tests/ -q` (repo root, as CI) → **114 passed, 2 warnings**.
- pyright (dev deps) on `src` → **0 errors, 0 warnings**.

## 4. Verified system invariants (from live observation)

1. **agent-service is read/proposal-only.** After HTTP cases B–F, `decisions=0`, `action_executions=0`
   in Postgres. The service never writes these tables; the row-write path lives in n8n
   Code/Postgres nodes (`approval_gate.py` docstring is explicit). ✓ confirmed, not assumed.
2. **Policy gate is deterministic & fail-closed.** Destructive text was DENY'd, not auto-allowed;
   refund over threshold forced APPROVAL despite a mock LLM that would otherwise default-report.
3. **Idempotency key is a pure function** of `(correlation_id, action_type, recommended_action)`.
   The server-side guarantee (collapse onto one DB row) is n8n/Postgres-side (see §5).
4. **Supervisor runs specialists concurrently and cites knowledge evidence** (SOP chunks surfaced
   in `knowledge_base`/`policy_rag` evidence for cases C/D).

## 5. Explicitly BLOCKED in this environment (not PASS)

| Thread | Reason | Impact |
|---|---|---|
| Real LLM (Cloudflare) | no credentials; mock reasoner used by default | only the executable **mock** path verified |
| n8n side-effects (Sheets/Gmail/Telegram/Ticket writes) | n8n not running | decision *proposals* verified; side-effect *execution* not |
| RabbitMQ event threads | component absent | not exercised |
| Postgres row-idempotency (unique collapse) | writer is n8n, not agent-service | DB healthy + empty; collapse logic not exercised |

Marked BLOCKED, not PASS — consistent with the QA plan's policy of not over-reporting.

## 6. Conclusion

The exercisable baseline is **green**: the executable mock path, the guardrail/policy/API layers,
and the supervisor/evidence plumbing all work end-to-end over live HTTP and match the 114-test
suite. Everything requiring external components (real LLM, n8n, RabbitMQ, DB-row writes) is
factually BLOCKED here and flagged, ready for a full-environment run.