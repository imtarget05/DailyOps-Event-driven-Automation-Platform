'use strict';

/**
 * Decision Loop + Safety Loop + Learning Loop — the "brain" of DailyOps.
 *
 * Policy Engine: matches events against rules to decide WHAT to do.
 * Safety Loop:   enforces action whitelist, dry-run, audit logging.
 * Learning Loop: records outcomes and adapts policy confidence.
 */

const crypto = require('crypto');

// ── Policy Engine ──────────────────────────────────────────────────────────
function normalizeEvent(event) {
  return {
    event_type: event.event_type,
    source: event.source,
    severity: event.severity || inferSeverity(event),
    service: event.service || event.payload?.service || 'unknown',
    alert: event.payload?.alert_name || event.event_type,
    environment: event.payload?.environment || 'production',
    ...event,
  };
}

function inferSeverity(event) {
  const type = event.event_type || '';
  if (type.includes('critical') || type.includes('down')) return 'critical';
  if (type.includes('warning') || type.includes('high')) return 'warning';
  return 'info';
}

function matchCondition(condition, event) {
  for (const [key, expected] of Object.entries(condition)) {
    const actual = key.split('.').reduce((o, k) => (o ? o[k] : undefined), event);
    if (actual === undefined) return false;
    if (Array.isArray(expected)) {
      if (!expected.includes(String(actual))) return false;
    } else if (typeof expected === 'object' && expected !== null) {
      if (expected.gt != null && !(Number(actual) > expected.gt)) return false;
      if (expected.gte != null && !(Number(actual) >= expected.gte)) return false;
      if (expected.lt != null && !(Number(actual) < expected.lt)) return false;
      if (expected.eq != null && String(actual) !== String(expected.eq)) return false;
      if (expected.regex != null && !new RegExp(expected.regex).test(String(actual))) return false;
    } else {
      if (String(actual) !== String(expected)) return false;
    }
  }
  return true;
}

function evaluatePolicies(event, policies) {
  for (const rule of policies) {
    if (rule.enabled === false) continue;
    if (matchCondition(rule.condition, event)) {
      return { matched: true, rule: rule.name, decision: rule.decision, safety: rule.safety || {} };
    }
  }
  return {
    matched: false,
    rule: 'default',
    decision: { actions: ['notify'], auto_approve: false },
    safety: {},
  };
}

module.exports = { normalizeEvent, inferSeverity, matchCondition, evaluatePolicies };
