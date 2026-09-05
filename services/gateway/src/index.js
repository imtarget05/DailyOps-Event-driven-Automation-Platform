'use strict';

const express = require('express');
const amqp = require('amqplib');
const crypto = require('crypto');

const config = require('./config');
const { registry, metrics } = require('./metrics');
const { Store } = require('./store');
const { Stats } = require('./stats');
const { validate, redact } = require('./validate');
const { assertTopology } = require('./topology');
const { dispatch } = require('./dispatch');
const notifier = require('./notifier');
const tickets = require('./tickets');

const log = (...a) => console.log(`[gateway ${new Date().toISOString()}]`, ...a);

const store = new Store();
const stats = new Stats();
const appLocals = { amqpChannel: null }; // populated in main()

// Global dispatch semaphore: bounds TOTAL in-flight n8n dispatches across
// ALL queues to MAX_CONCURRENCY (RabbitMQ prefetch alone is per-queue, so a
// 3-queue fan-out could otherwise reach 3x the cap).
class Semaphore {
  constructor(max) {
    this.max = max;
    this.active = 0;
    this.waiters = [];
  }
  acquire() {
    return new Promise((resolve) => {
      if (this.active < this.max) {
        this.active += 1;
        resolve();
        return;
      }
      this.waiters.push(resolve);
    });
  }
  release() {
    const next = this.waiters.shift();
    if (next) {
      this.active += 1; // keep it full
      next();
    } else {
      this.active -= 1;
    }
  }
}
const semaphore = new Semaphore(config.maxConcurrency);

// ------------------------------------------------------------------ helpers
const statusFromType = (t = '') => (t.includes('failed') ? 'failed' : t.includes('skipped') ? 'skipped' : 'completed');

function nowEvent(partial) {
  return {
    event_id: `evt_${crypto.randomUUID()}`,
    source: 'dailyops.gateway',
    timestamp: new Date().toISOString(),
    payload: {},
    ...partial,
  };
}

async function publishEvent(event) {
  if (!appLocals.amqpChannel) return false;
  appLocals.amqpChannel.publish(config.exchange, event.event_type, Buffer.from(JSON.stringify(event)), {
    contentType: 'application/json',
    persistent: true,
  });
  return true;
}

async function deadletter(reason, originalEvent, queue, detail) {
  metrics.deadletterTotal.inc({ reason });
  log(`DEAD-LETTER reason=${reason} queue=${queue || '-'} event=${(originalEvent && originalEvent.event_id) || '?'}`);
  if (!appLocals.amqpChannel) return;
  // raw copy to the DLQ + escalation event routed to q.n8n.notify
  const raw =
    originalEvent === undefined
      ? Buffer.from(JSON.stringify({ reason, detail }))
      : Buffer.from(typeof originalEvent === 'string' ? originalEvent : JSON.stringify(originalEvent));
  appLocals.amqpChannel.publish(config.dlx, `failed.${queue || 'unknown'}`, raw, { persistent: true });
  await publishEvent(
    nowEvent({
      event_type: 'system.deadletter',
      source: 'dailyops.gateway',
      severity: 'critical',
      payload: { reason, queue, detail, original_event_id: originalEvent && originalEvent.event_id },
    })
  );
}

// ------------------------------------------------------------ event handler
async function handleMessage(queue, msg) {
  const { workflow, webhook } = config.queues[queue];
  const channel = appLocals.amqpChannel;
  const finish = () => {
    if (channel) channel.ack(msg);
  };

  let event;
  try {
    event = JSON.parse(msg.content.toString());
  } catch (e) {
    await deadletter('invalid_json', msg.content.toString(), queue, e.message);
    return finish();
  }

  const invalid = validate(event);
  if (invalid) {
    metrics.eventsInvalid.inc();
    await deadletter('invalid_event', event, queue, invalid);
    return finish();
  }

  // security (section 13): events carry IDs + metadata only; strip anything sensitive
  const redactedCount = redact(event);
  if (redactedCount) metrics.eventsRedacted.inc(redactedCount);

  metrics.eventsReceived.inc({ source: event.source, event_type: event.event_type });
  const consumedAt = Date.now();
  metrics.brokerLatency.observe({ source: event.source }, Math.max(0, (consumedAt - Date.parse(event.timestamp)) / 1000));

  // ---- idempotency (section 8): per queue, event_id processed exactly once
  const fresh = await store.setnx(`idem:${queue}:${event.event_id}`, 'processing', 86400);
  if (!fresh) {
    metrics.eventsIdempotentSkipped.inc();
    log(`SKIP idempotent event_id=${event.event_id} (already processed)`);
    stats.record({ event_id: event.event_id, event_type: event.event_type, workflow, skipped: 'idempotent' });
    return finish();
  }

  // ---- deduplication (section 9): dedup_key + suppression window
  let dKey = event.dedup_key || null;
  let dWindow = config.suppressionWindowS;
  if (event.dedup && typeof event.dedup === 'object') {
    dKey = event.dedup.key || dKey;
    dWindow = Number(event.dedup.window_s) || dWindow;
  }
  if (dKey) {
    const acquired = await store.setnx(`dedup:${queue}:${dKey}`, event.event_id, dWindow);
    if (!acquired) {
      metrics.eventsDeduplicated.inc();
      log(`SKIP deduplicated key=${dKey} window=${dWindow}s event_id=${event.event_id}`);
      stats.record({ event_id: event.event_id, event_type: event.event_type, workflow, skipped: 'deduplicated', dedup_key: dKey });
      return finish();
    }
  }

  // ---- dispatch to n8n (concurrency bounded per-queue by RabbitMQ prefetch
  // ---- AND globally by the gateway semaphore == MAX_CONCURRENCY)
  stats.begin(event, workflow);
  const inflight = stats.inflight.get(stats.key(workflow, event.event_id));
  if (inflight) inflight.workflow = workflow;
  await semaphore.acquire();
  metrics.activeExecutions.inc({ workflow });
  let result;
  try {
    result = await dispatch({
      url: `${config.n8nBaseUrl}${webhook}`,
      body: event,
      workflow,
      metrics,
      retryMax: config.retryMax,
      backoffMs: config.retryBackoffMs,
      timeoutMs: config.dispatchTimeoutMs,
      log,
    });
  } finally {
    metrics.activeExecutions.dec({ workflow });
    semaphore.release();
  }

  if (result.ok) {
    metrics.eventsDispatched.inc({ workflow });
    // section 8: persist event_id -> execution_id mapping
    await store.set(
      `idem:exec:${event.event_id}`,
      JSON.stringify({ execution_id: result.executionId, workflow, at: new Date().toISOString() }),
      604800
    );
    stats.markDispatched(event.event_id, workflow);
    stats.record({
      event_id: event.event_id,
      event_type: event.event_type,
      workflow,
      broker_ms: Math.max(0, consumedAt - Date.parse(event.timestamp)),
      dispatch_ms: result.latencyMs,
      attempt: result.attempt,
      execution_id: result.executionId,
    });
    log(`DISPATCHED ${event.event_type} event=${event.event_id} -> ${workflow} in ${result.latencyMs}ms (attempt ${result.attempt})`);
  } else {
    metrics.eventsFailed.inc({ workflow });
    await deadletter('dispatch_failed', event, queue, result.error);
  }
  finish();
}

// ------------------------------------------------------- queue depth polling
async function startQueueDepthPoller() {
  if (!config.rabbitMgmtUrl) return;
  const auth = Buffer.from(`${config.rabbitMgmtUser}:${config.rabbitMgmtPass}`).toString('base64');
  const poll = async () => {
    try {
      const res = await fetch(`${config.rabbitMgmtUrl}/api/queues`, {
        headers: { authorization: `Basic ${auth}` },
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) return;
      const queues = await res.json();
      for (const q of queues) metrics.queueDepth.set({ queue: q.name }, q.messages || 0);
    } catch {
      /* mgmt API not ready yet */
    }
  };
  poll();
  setInterval(poll, 10_000);
}

// ------------------------------------------------------------------ HTTP API
const app = express();
app.use(express.json({ limit: '1mb' }));

app.get('/health', (_req, res) => res.json({ status: 'ok', uptime: process.uptime(), broker: !!appLocals.amqpChannel }));

app.get('/metrics', async (_req, res) => {
  res.set('content-type', registry.contentType);
  res.end(await registry.metrics());
});

app.get('/stats', (_req, res) => res.json(stats.json()));

// workflows publish result events here (n8n has no built-in AMQP publisher)
app.post('/publish', async (req, res) => {
  const event = req.body;
  console.log(`[/publish] received event_type=${event && event.event_type} correlation_id=${event && event.correlation_id} correlation_workflow=${event && event.correlation_workflow}`);
  const invalid = validate(event);
  if (invalid) return res.status(400).json({ published: false, error: invalid });

  const redactedCount = redact(event);
  if (redactedCount) metrics.eventsRedacted.inc(redactedCount);

  // result event for a tracked original -> compute event-to-action latency (section 11)
  const correlationKey =
    event.correlation_id && event.correlation_workflow
      ? stats.key(event.correlation_workflow, event.correlation_id)
      : null;
  if (correlationKey && stats.inflight.has(correlationKey)) {
    const orig = stats.inflight.get(correlationKey);
    const now = Date.now();
    const totalMs = now - orig.timestamp;
    const workflowMs = orig.dispatchedAt ? now - orig.dispatchedAt : null;
    const status = statusFromType(event.event_type);
    metrics.eventToActionLatency.observe({ event_type: orig.event_type }, totalMs / 1000);
    if (workflowMs !== null) metrics.workflowDuration.observe({ workflow: orig.workflow, status }, workflowMs / 1000);
    metrics.eventsProcessed.inc({ workflow: orig.workflow, status });
    if (status === 'failed') metrics.eventsFailed.inc({ workflow: orig.workflow });
    stats.end(correlationKey);
    stats.record({
      event_id: event.correlation_id,
      result_event: event.event_type,
      workflow: orig.workflow,
      event_type: orig.event_type,
      status,
      total_ms: totalMs,
      workflow_ms: workflowMs,
    });
    log(`RESULT ${event.event_type} for ${event.correlation_id} total=${totalMs}ms workflow=${workflowMs}ms status=${status}`);
  }

  const ok = await publishEvent(event);
  res.json({ published: ok, event_id: event.event_id, routing_key: event.event_type });
});

app.post('/notify', async (req, res) => {
  const { channel = 'telegram', text, subject } = req.body || {};
  if (!text) return res.status(400).json({ error: 'text is required' });
  try {
    const result = await notifier.send({ channel, text, subject });
    if (result.sent) metrics.notificationsSent.inc({ channel: result.channel });
    else metrics.notificationsSkipped.inc();
    res.json(result);
  } catch (e) {
    metrics.notificationsSkipped.inc();
    res.status(502).json({ sent: false, error: e.message });
  }
});

app.post('/tickets', async (req, res) => {
  const { title, description = '', severity, event } = req.body || {};
  if (!title) return res.status(400).json({ error: 'title is required' });
  try {
    const t = await tickets.create({ store, title, description, severity, event });
    metrics.ticketsCreated.inc({ backend: t.backend });
    log(`TICKET created ${t.key} backend=${t.backend}`);
    res.json(t);
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});

app.get('/tickets', async (_req, res) => {
  const rows = await store.lrange('tickets', 0, -1);
  res.json(rows.map((r) => JSON.parse(r)).reverse());
});

// ------------------------------------------------------------------- startup
async function main() {
  await store.connect(config.redisUrl);

  if (config.rabbitUrl) {
    const conn = await amqp.connect(config.rabbitUrl);
    conn.on('error', (e) => {
      log('rabbitmq connection error:', e.message);
      process.exit(1); // container restarts and reconnects
    });
    conn.on('close', () => log('rabbitmq connection closed'));

    const setup = await conn.createChannel();
    await assertTopology(setup, log);
    await setup.close();

    const consumeCh = await conn.createChannel();
    await consumeCh.prefetch(config.maxConcurrency); // backpressure == concurrency cap
    appLocals.amqpChannel = consumeCh;

    for (const queue of Object.keys(config.queues)) {
      consumeCh.consume(queue, (msg) => {
        if (!msg) return;
        handleMessage(queue, msg).catch(async (e) => {
          log(`handler error on ${queue}:`, e.message);
          try {
            consumeCh.nack(msg, false, false);
          } catch {
            /* already acked */
          }
        });
      });
      log(`consuming ${queue} (prefetch=${config.maxConcurrency})`);
    }
    startQueueDepthPoller();
  } else {
    log('RABBITMQ_URL empty — broker mode disabled (HTTP API + metrics only)');
  }

  app.listen(config.port, () => log(`HTTP API listening on :${config.port} (metrics at /metrics)`));
}

if (require.main === module) {
  main().catch((e) => {
    console.error('[gateway] fatal:', e);
    process.exit(1);
  });
}

module.exports = { app, handleMessage, publishEvent };

