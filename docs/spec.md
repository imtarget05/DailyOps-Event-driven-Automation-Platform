# Product Spec: DailyOps — Agentic Business Operations Platform

> **Status**: Draft
> **Companion research**: `docs/researches/positioning.md`

## 1. Product Intent

DailyOps is an **agentic business operations platform** that turns business events and
requests into **governed, auditable actions**. It is not an AI chatbot and not plain
workflow automation: an LLM *decides and proposes*, a deterministic policy engine *gates*,
humans *approve* high-risk actions, the system *executes*, *verifies*, and *audits*.

Central engineering problem (from README §Problem): an autonomous operations agent must
be able to *propose* real-world actions (notify, ticket, execute) without ever being able to
*self-authorize* those actions.

## 2. Users

- **Ops / Operations staff** — receive AI-detected issues (inventory, refunds, maintenance)
  and approve or reject proposed actions.
- **Finance** — review and approve refund / large-money actions against policy.
- **Business decision-makers** — consume daily reports and inventory/advisory insights.
- **Platform integrators / n8n operators** — configure triggers, channels, and credentials;
  execute the proposed actions behind the policy gate.

## 3. Core Workflow

```
Event ─► AI Decision ─► Policy ─► Human Approval ─► Execute ─► Verify ─► Audit / Learn
```

Concretely: `POST /agent/decide` runs the supervisor
(classify → parallel specialists → aggregate) → LLM proposal is stripped of any
self-authorization claims → the deterministic policy engine returns
**ALLOW / APPROVAL / DENY** → n8n routes execution accordingly.

## 4. Flagship Business Flows

| Business | What DailyOps does | Flow |
|---|---|---|
| **Inventory Management** | Detect stock-outs, assess severity, propose/approve restock, track outcome | Event → Analyze → Decide → Approve → Restock → Verify |
| **Financial Transactions** | Process refunds, check conditions, require approval before execution | Request → Analyze → Policy → Approval → Execute → Verify |
| **System Maintenance** | Handle risky ops (disable/delete) behind policy + approval | Alert/Request → Assess → Policy → Approval → Execute → Verify |

## 5. Non-negotiable Constraints (verified invariants)

1. **LLM never self-authorizes.** Injected `authorized/approved/needs_approval` claim keys are
   stripped (`assert_no_authorization_claims`, DAILYOPS-02); semantic claim language is classified
   and fail-closed (NONE / SUSPICIOUS / PRIVILEGE_ESC, DAILYOPS-11).
2. **Deterministic policy gate.** Verdicts come from `policies.yaml`, never from the LLM.
   Destructive actions (delete/drop/truncate/shutdown…) are **DENY'd** regardless of LLM label.
3. **Human approval for high-risk.** EXECUTE and over-threshold actions require approval; the
   gate has a TTL → auto-ESCALATION (DAILYOPS-03/04). Approvals are literal approve/reject only.
4. **Read/proposal-only core.** agent-service only *proposes*; n8n *executes* behind the gate,
   so side effects cannot leak from the reasoner.
5. **Idempotency.** Deterministic `idempotency_key` collapses replays/retries onto one action
   (DAILYOPS-05); retry safety classifies `UNKNOWN_OUTCOME` vs `FAILED` (DAILYOPS-08).
6. **Verify + audit.** Every action carries end-to-end `correlation_id`
   (event → decision → action → verify → close); `verifier` checks real external state.

## 6. Acceptance Scenarios (agent-service layer — live-verified)

| # | Scenario | Expected |
|---|---|---|
| A | `GET /health` | 200 `{"status":"ok"}` |
| B | daily report | → REPORT, `report_auto`, SUCCESS, specialists run |
| C | low-stock inventory | → TICKET restock_200, `restock_auto_threshold`, ALLOW, evidence cited |
| D | refund > threshold | → EXECUTE, `finance_refund_approval`, **APPROVAL_REQUIRED** |
| E | destructive request | → **DENY**, `destructive_action_deny` (fail-closed) |
| F | idempotency | same request → identical `idempotency_key` |
| G | auth enforced | no/wrong key → 401; correct key → 200; `/health` open 200 |
| H | smalltalk | NOTIFY, zero specialists, friendly reply |
| I | advisory request | REPORT (council only), `advisory_report_only`, never TICKET/EXECUTE |

Automated cross-check: `pytest agent-service/tests/ -q` → 165 passed; pyright → 0 errors.

## 7. Out of Scope / Environment-Blocked (pending full env)

- Real LLM (Cloudflare) — needs credentials (mock reasoner used by default).
- n8n side-effect execution, RabbitMQ threads, Postgres row-writes/idempotency collapse —
  need n8n + broker running.
See `docs/researches/qa-report-phase9.md` for the blocked register.

## 8. Definition of Done for this slice

- `docs/spec.md` and `docs/researches/positioning.md` reflect the agentic-ops positioning.
- All non-negotiables above map to verified code paths and live-tested scenarios.
