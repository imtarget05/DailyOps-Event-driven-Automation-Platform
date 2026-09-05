'use strict';

const crypto = require('crypto');

/** Builds a spec-compliant event (section 3 of the DailyOps spec). */
function makeEvent({ event_type, source, payload = {}, dedup_key = null, severity, service, event_id }) {
  return {
    event_id: event_id || `evt_${crypto.randomUUID()}`,
    event_type,
    source,
    timestamp: new Date().toISOString(),
    correlation_id: crypto.randomUUID(),
    ...(severity ? { severity } : {}),
    ...(service ? { service } : {}),
    ...(dedup_key ? { dedup_key } : {}),
    payload,
  };
}

/** A monitoring alert that fans out to auto-fix + ticket + notify workflows. */
function serviceAlert({ service = 'api', alert = 'service_down', severity = 'critical', pod, dedup_key, event_id }) {
  return makeEvent({
    event_type: 'service.alert',
    source: 'prometheus',
    severity,
    service,
    dedup_key: dedup_key || `${service}:${alert}:${pod || 'shared'}`,
    payload: {
      alert_name: alert,
      instance: `${service}.prod.svc:8080`,
      pod: pod || 'api-7d9f',
      namespace: 'production',
      remediation: 'restart_pod',
    },
    event_id,
  });
}

function hermesTaskCompleted(taskId = `task_${crypto.randomUUID().slice(0, 8)}`) {
  return makeEvent({
    event_type: 'task.completed',
    source: 'hermes',
    payload: { task_id: taskId, duration_ms: 4210, status: 'success' },
  });
}

function maiaDocumentIndexed(docId = `doc_${crypto.randomUUID().slice(0, 8)}`) {
  return makeEvent({
    event_type: 'document.indexed',
    source: 'maia',
    payload: { document_id: docId, chunks: 42, index: 'knowledge-base' },
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ metrics
async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
}

async function fetchMetricsText(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.text();
}

/** Sum of dailyops_active_executions across workflows, from /metrics text. */
function parseActiveExecutions(text) {
  let total = 0;
  for (const line of text.split('\n')) {
    if (line.startsWith('dailyops_active_executions{')) {
      const v = Number(line.split('}')[1]);
      if (Number.isFinite(v)) total += v;
    }
  }
  return total;
}

function percentile(sorted, p) {
  if (!sorted.length) return null;
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, idx)];
}

function summarize(samples) {
  const nums = samples.filter(Number.isFinite).sort((a, b) => a - b);
  return {
    n: nums.length,
    min: nums[0] ?? null,
    p50: percentile(nums, 50),
    p95: percentile(nums, 95),
    p99: percentile(nums, 99),
    max: nums[nums.length - 1] ?? null,
  };
}

const fmt = (v) => (v === null ? '-' : `${Math.round(v)}ms`);

module.exports = {
  makeEvent,
  serviceAlert,
  hermesTaskCompleted,
  maiaDocumentIndexed,
  sleep,
  fetchJson,
  fetchMetricsText,
  parseActiveExecutions,
  summarize,
  fmt,
};
