'use strict';

/**
 * Safety Loop + Evaluation Loop + Learning Loop.
 */

const crypto = require('crypto');

// ── Safety Loop ────────────────────────────────────────────────────────────
class SafetyEngine {
  constructor(config, auditLog) {
    this.whitelist = config.action_whitelist || {};
    this.dryRun = config.dry_run || false;
    this.requireApproval = config.require_approval || [];
    this.audit = auditLog;
  }

  isActionAllowed(action) {
    const entry = this.whitelist[action];
    if (!entry) return { allowed: true, risk: 'low' }; // unknown actions: safe by default
    if (entry.allowed === false) return { allowed: false, reason: `action '${action}' is blocked` };
    return { allowed: true, risk: entry.risk || 'unknown' };
  }

  requiresApproval(action, risk) {
    if (this.requireApproval.includes(action)) return true;
    if (risk === 'critical' || risk === 'high') return true;
    return false;
  }

  recordAudit(entry) {
    this.audit.push({
      id: `audit_${crypto.randomUUID()}`,
      timestamp: new Date().toISOString(),
      ...entry,
    });
    if (this.audit.length > 5000) this.audit.splice(0, this.audit.length - 5000);
  }

  gate(action, context) {
    const check = this.isActionAllowed(action);
    if (!check.allowed) {
      this.recordAudit({ action, decision: 'blocked', reason: check.reason, event_id: context.event_id });
      return { decision: 'blocked', reason: check.reason };
    }
    if (this.requiresApproval(action, check.risk)) {
      this.recordAudit({ action, decision: 'approval_required', risk: check.risk, event_id: context.event_id });
      return { decision: 'approval_required', risk: check.risk };
    }
    if (this.dryRun) {
      this.recordAudit({ action, decision: 'dry_run', risk: check.risk, event_id: context.event_id });
      return { decision: 'dry_run', risk: check.risk };
    }
    this.recordAudit({ action, decision: 'approved', risk: check.risk, event_id: context.event_id });
    return { decision: 'approved', risk: check.risk };
  }
}

// ── Evaluation Loop ────────────────────────────────────────────────────────
class Evaluator {
  constructor() {
    this.window = [];
    this.maxWindow = 5000;
  }

  record(outcome) {
    outcome.t = Date.now();
    this.window.push(outcome);
    if (this.window.length > this.maxWindow) this.window.splice(0, this.window.length - this.maxWindow);
  }

  mttr() {
    const resolved = this.window.filter((o) => o.resolved && o.duration_ms);
    if (!resolved.length) return null;
    return resolved.reduce((s, o) => s + o.duration_ms, 0) / resolved.length;
  }

  stats() {
    const total = this.window.length;
    if (!total) return { total: 0 };
    const autoFixed = this.window.filter((o) => o.outcome === 'auto_fixed').length;
    const escalated = this.window.filter((o) => o.outcome === 'escalated').length;
    const failed = this.window.filter((o) => o.outcome === 'failed').length;
    const suppressed = this.window.filter((o) => o.outcome === 'suppressed').length;
    const blocked = this.window.filter((o) => o.outcome === 'blocked').length;
    const dryRun = this.window.filter((o) => o.outcome === 'dry_run').length;
    const retries = this.window.filter((o) => o.retries > 0).length;
    const actions = this.window.filter((o) => o.action_taken).length;
    const successes = this.window.filter((o) => o.action_success).length;
    return {
      total,
      auto_fixed: autoFixed,
      escalated,
      failed,
      suppressed,
      blocked,
      dry_run: dryRun,
      retries,
      auto_resolution_rate: autoFixed / total,
      escalation_rate: escalated / total,
      failure_rate: failed / total,
      action_success_rate: actions ? successes / actions : null,
      mttr_ms: this.mttr(),
    };
  }
}

// ── Learning Loop ──────────────────────────────────────────────────────────
class Learner {
  constructor(store) {
    this.store = store;
    this.feedback = [];
  }

  async recordOutcome(eventId, outcome) {
    this.feedback.push({ event_id: eventId, outcome, t: Date.now() });
    await this.store.rpush('learning:outcomes', JSON.stringify({ event_id: eventId, outcome, t: new Date().toISOString() }));
  }

  async recordFeedback(eventId, feedback) {
    await this.store.rpush('learning:feedback', JSON.stringify({ event_id: eventId, ...feedback, t: new Date().toISOString() }));
    this.feedback.push({ event_id: eventId, feedback });
  }

  async getInsights(store) {
    const outcomes = await store.lrange('learning:outcomes', 0, -1);
    const byAction = {};
    for (const o of outcomes) {
      const parsed = JSON.parse(o);
      const action = parsed.outcome.action_taken || 'unknown';
      if (!byAction[action]) byAction[action] = { success: 0, fail: 0 };
      if (parsed.outcome.action_success) byAction[action].success++;
      else byAction[action].fail++;
    }
    const insights = [];
    for (const [action, counts] of Object.entries(byAction)) {
      const total = counts.success + counts.fail;
      if (total >= 3 && counts.fail / total > 0.5) {
        insights.push({
          action,
          failure_rate: counts.fail / total,
          suggestion: `Consider gating '${action}' — failure rate ${(counts.fail / total * 100).toFixed(0)}%`,
        });
      }
    }
    return insights;
  }
}

module.exports = { SafetyEngine, Evaluator, Learner };
