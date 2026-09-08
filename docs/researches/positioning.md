# Positioning — DailyOps

> **Status**: First-party positioning (repo-fact-sourced)
> **Date**: 2026-09-08
> **Basis**: Verified codebase facts (this branch) + product brief. External competitor
> research is pending cloud-search credits (`/exa-search` blocked: Cline 402). Rows marked
> **model-knowledge** are directional and must be validated before external use.

## One-line positioning

> **DailyOps is an agentic business-operations platform that turns inventory, financial,
> and system-maintenance events into governed, auditable actions — AI decides, policy gates,
> humans approve, the system executes and verifies.**

Shorter: **"AI that doesn't just decide what should happen — it safely gets it done."**

## What DailyOps is NOT

- **Not an AI chatbot** — it acts on external systems, not just answers.
- **Not plain n8n/Zapier automation** — those orchestrate workflow; DailyOps adds an AI
  decision+policy gate so *what* runs and *who approves* is governed and auditable.
- **Not an ungoverned agent** — the LLM can propose but can never self-authorize (see Non-negotiables).

## The business problem

Enterprises run event-driven workflows shaped like:

```
Event → human analyzes → check policy → request approval → execute on system → verify → audit
```

DailyOps compresses the manual parts (analysis, policy check, ticket/notification work) with
an AI decision layer, while **keeping the governance boundaries that make automation safe**:

```
Event → AI Decision → Policy → Human Approval → Execute → Verify → Audit / Learn
```

## The three flagship business flows (verified wiring in `supervisor.py` + `policy_engine.py`)

| Business | What DailyOps does | Flow |
|---|---|---|
| **Inventory Management** | Detect stock-outs, assess severity, propose/approve restocks, track outcome | Event → Analyze → Decide → Approve → Restock → Verify |
| **Financial Transactions** | Process refund requests, check conditions, require approval before execution | Request → Analyze → Policy → Approval → Execute → Verify |
| **System Maintenance** | Handle risky ops (disable/delete service/resource) behind policy + approval | Alert/Request → Assess → Policy → Approval → Execute → Verify |

## The three problems DailyOps solves

1. **Reduce manual operational work** — repeatable checks (inventory, refund analysis, ticket
   creation, notifications) are AI-assisted and coordinated by the supervisor + specialist agents.
2. **Reduce AI-automation risk** — the LLM is explicitly engineered to never execute on its own:
   `LLM → Policy → Approval → Execute → Verify` is enforced in code.
3. **Turn automation into governed workflow** — every action carries
   `decision → authorization → approval → idempotency → execution → verification → audit`.

## Differentiators (verified in the codebase)

| Claim | Code evidence (this branch) |
|---|---|
| LLM never self-authorizes | `assert_no_authorization_claims()` strips injected `authorized/approved/needs_approval` (DAILYOPS-02); `_enforce_auth_claims()` classifies semantic claims (NONE/SUSPICIOUS/PRIVILEGE_ESC → fail-closed) (DAILYOPS-11) |
| Deterministic policy gate | `policy_engine.check()` reads only `policies.yaml` thresholds; destructive text → **DENY** regardless of LLM label; over-threshold refund/restock → **APPROVAL** |
| Human approval boundary | high-risk/EXECUTE actions never auto-execute; `approval_gate` (TTL → auto-ESCALATE); literal approve/reject only (DAILYOPS-03/04) |
| Idempotent execution | deterministic `idempotency_key = sha256(correlation_id, action, recommendation)` → replays collapse (DAILYOPS-05) |
| Read/proposal-only core | agent-service *proposes*; n8n *executes* behind the gate → side effects can't leak from the reasoner |
| Verify + audit | events → decision → action → verify → close, correlated end-to-end; `verifier` checks real external state, not HTTP 200 |

## Landscape map (model-knowledge — validate before quoting)

| Category | Examples | Gap DailyOps fills |
|---|---|---|
| Workflow automation | Zapier, Make, n8n | Execution **without** governed AI decision |
| Agent frameworks | LangGraph, CrewAI, Agents SDK | Build blocks, no ops approval/denial semantics |
| Guardrails libs | NeMo Guardrails, Guardrails AI | Text rules, not *business-action* gating |
| HITL agent tools | CopilotKit, Braintrust | General approval, not policy-gated execution |
| OPS/ITSM | PagerDuty, ServiceNow | Human-run ops, not LLM-agentic decisioning |

**Whitespace**: the intersection of *deterministic policy engine + human-approval boundary +
idempotent/auditable execution wrapped around a supervised LLM decision*.

## Next steps

- Re-run `/exa-search` once cloud-search credits are available to add live, cited competitor
  and trend references to this document.
- Optionally promote the verified differentiators into `docs/spec.md` under constraints.