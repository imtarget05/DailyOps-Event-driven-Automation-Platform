'use strict';

/**
 * Scenario runner for DailyOps demos:
 *   node simulator.js happy        3 normal events (hermes/maia/monitoring)
 *   node simulator.js dedup        50 identical alerts -> exactly 1 workflow
 *   node simulator.js concurrency  10 alerts -> 10 concurrent executions (max 8)
 *   node simulator.js latency      N events -> event-to-action latency report
 *   node simulator.js failure      permanent pod failure -> retry/failed/ticket
 *   node simulator.js poison       invalid events -> validation + dead-letter
 *   node simulator.js all          run everything
 */
const amqp = require('amqplib');
const {
  serviceAlert,
  hermesTaskCompleted,
  maiaDocumentIndexed,
  sleep,
  fetchJson,
  fetchMetricsText,
  parseActiveExecutions,
  summarize,
  fmt,
} = require('./events');

const AMQP_URL = process.env.AMQP_URL || '';
const GATEWAY = process.env.GATEWAY_URL || 'http://localhost:9090';

async function withChannel(fn) {
  const conn = await amqp.connect(AMQP_URL);
  try {
    const ch = await conn.createChannel();
    await ch.assertExchange('dailyops.events', 'topic', { durable: true });
    return await fn(ch);
  } finally {
    await conn.close();
  }
}

function publish(ch, event) {
  ch.publish('dailyops.events', event.event_type, Buffer.from(JSON.stringify(event)), {
    contentType: 'application/json',
    persistent: true,
  });
  console.log(`published ${event.event_type} event_id=${event.event_id}${event.dedup_key ? ` dedup=${event.dedup_key}` : ''}`);
}

// ------------------------------------------------------------------ scenarios
async function scenarioHappy(ch) {
  console.log('\n=== scenario: happy path (3 events from 3 producers) ===');
  publish(ch, hermesTaskCompleted());
  publish(ch, maiaDocumentIndexed());
  publish(ch, serviceAlert({ pod: 'api-7d9f' }));
  await sleep(6000);
  const stats = await fetchJson(`${GATEWAY}/stats`);
  console.log('\nlatency samples so far:', JSON.stringify(summarize(stats.recent.filter((s) => s.total_ms).map((s) => s.total_ms))));
}

async function scenarioDedup(ch) {
  console.log('\n=== scenario: dedup (50 identical alerts -> 1 workflow) ===');
  const dedupKey = `api:service_down:dedup-demo-${Date.now()}`;
  for (let i = 0; i < 50; i++) {
    publish(ch, serviceAlert({ pod: 'api-7d9f', dedup_key: dedupKey }));
    await sleep(30);
  }
  await sleep(4000);
  const m = await fetchMetricsText(`${GATEWAY}/metrics`);
  const deduped = Number(m.match(/dailyops_events_deduplicated_total(?:\{[^}]*\})? (\d+)/)?.[1] || 0);
  console.log(`\ndailyops_events_deduplicated_total = ${deduped} (expected: 49 of 50 dropped)`);
}

async function scenarioConcurrency(ch) {
  console.log('\n=== scenario: concurrency (10 events -> max 8 concurrent executions) ===');
  let maxActive = 0;
  let stop = false;
  const poller = (async () => {
    while (!stop) {
      try {
        const text = await fetchMetricsText(`${GATEWAY}/metrics`);
        maxActive = Math.max(maxActive, parseActiveExecutions(text));
      } catch {
        /* gateway busy */
      }
      await sleep(120);
    }
  })();
  for (let i = 0; i < 10; i++) {
    publish(ch, serviceAlert({ pod: 'api-7d9f', dedup_key: `api:concurrency:${Date.now()}:${i}` }));
    await sleep(50);
  }
  await sleep(12000);
  stop = true;
  await poller;
  console.log(`\nmax observed dailyops_active_executions = ${maxActive} (expect <= MAX_CONCURRENCY=8, >1 proves concurrency)`);
}

async function scenarioLatency(ch, n = 10) {
  console.log(`\n=== scenario: latency (${n} events, event-to-action) ===`);
  for (let i = 0; i < n; i++) {
    publish(ch, serviceAlert({ pod: 'api-7d9f', dedup_key: `api:latency:${Date.now()}:${i}` }));
    await sleep(200);
  }
  await sleep(9000);
  const stats = await fetchJson(`${GATEWAY}/stats`);
  const totals = stats.recent.filter((s) => s.total_ms).map((s) => s.total_ms);
  const broker = stats.recent.filter((s) => Number.isFinite(s.broker_ms)).map((s) => s.broker_ms);
  const dispatch = stats.recent.filter((s) => Number.isFinite(s.dispatch_ms)).map((s) => s.dispatch_ms);
  const t = summarize(totals);
  const b = summarize(broker);
  const d = summarize(dispatch);
  console.log('\nevent-to-action latency (last samples):');
  console.log(`  total:          ${fmt(t.p50)} p50 | ${fmt(t.p95)} p95 | ${fmt(t.max)} max  (n=${t.n})`);
  console.log(`  broker latency: ${fmt(b.p50)} p50 | ${fmt(b.p95)} p95`);
  console.log(`  dispatch->n8n:  ${fmt(d.p50)} p50 | ${fmt(d.p95)} p95`);
  console.log('\nvs old polling architecture: 0-300s. New: ~seconds.');
}

async function scenarioFailure(ch) {
  console.log('\n=== scenario: permanent failure (bad-pod -> auto_fix.failed -> ticket) ===');
  publish(ch, serviceAlert({ pod: 'bad-pod', dedup_key: `api:failure:${Date.now()}` }));
  await sleep(8000);
  const tickets = await fetchJson(`${GATEWAY}/tickets`);
  const latest = tickets[0] ? { key: tickets[0].key, severity: tickets[0].severity, title: tickets[0].title } : null;
  console.log(`\ntickets on record: ${tickets.length}, latest: ${JSON.stringify(latest)}`);
}

async function scenarioPoison(ch) {
  console.log('\n=== scenario: poison events (invalid JSON / missing fields) ===');
  ch.publish('dailyops.events', 'service.alert', Buffer.from('this is not json{'), { persistent: true });
  console.log('published raw invalid JSON');
  publish(ch, {
    event_type: 'service.alert',
    source: 'prometheus',
    timestamp: new Date().toISOString(),
    payload: {},
  });
  console.log('published event missing event_id (rejected by validation -> dead-letter)');
  await sleep(3000);
  const m = await fetchMetricsText(`${GATEWAY}/metrics`);
  const dl = m.match(/dailyops_deadletter_total(?:\{[^}]*\})? (\d+)/)?.[1];
  const inv = m.match(/dailyops_events_invalid_total(?:\{[^}]*\})? (\d+)/)?.[1];
  console.log(`\ndailyops_deadletter_total = ${dl || 0}`);
  console.log(`dailyops_events_invalid_total = ${inv || 0}`);
}

async function scenarioSafety(ch) {
  console.log('\n=== scenario: safety loop (blocked + approval-required actions) ===');
  // This event should match pod-crashloop-auto-restart policy (restart_pod allowed + auto_approve)
  publish(ch, serviceAlert({ pod: 'api-7d9f', dedup_key: `safety:allowed:${Date.now()}` }));
  await sleep(3000);
  const m = await fetchMetricsText(`${GATEWAY}/metrics`);
  const blocks = Number(m.match(/dailyops_safety_blocks_total(?:\{[^}]*\})? (\d+)/)?.[1] || 0);
  const approved = Number(m.match(/dailyops_events_dispatched_total(?:\{workflow="auto-fix"\})? (\d+)/)?.[1] || 0);
  console.log(`\nsafety blocks=${blocks}, auto-fix dispatched=${approved} (expect approved > 0 for allowed restart_pod)`);
}

async function scenarioEvaluation(ch) {
  console.log('\n=== scenario: evaluation loop (auto-resolution rate + MTTR) ===');
  // Generate some events first
  for (let i = 0; i < 5; i++) {
    publish(ch, serviceAlert({ pod: 'api-7d9f', dedup_key: `eval:${Date.now()}:${i}` }));
    await sleep(200);
  }
  await sleep(8000);
  const evalRes = await fetchJson(`${GATEWAY}/evaluation`);
  console.log('\nevaluation stats:', JSON.stringify(evalRes, null, 0));
}

async function scenarioLearning(ch) {
  console.log('\n=== scenario: learning loop (feedback + insights) ===');
  // Submit feedback for a past event
  await fetch(`${GATEWAY}/learning/feedback`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ event_id: 'evt_learning_demo', correct_action: 'restart_pod', comment: 'correct, pod recovered', rating: 5 }),
  }).then((r) => r.json()).then((d) => console.log('feedback recorded:', JSON.stringify(d)));
  await sleep(500);
  const insights = await fetchJson(`${GATEWAY}/learning/insights`);
  console.log('\nlearning insights:', JSON.stringify(insights, null, 0));
}

const scenarios = {
  happy: scenarioHappy,
  dedup: scenarioDedup,
  concurrency: scenarioConcurrency,
  latency: scenarioLatency,
  failure: scenarioFailure,
  poison: scenarioPoison,
  safety: scenarioSafety,
  evaluation: scenarioEvaluation,
  learning: scenarioLearning,
};

async function main() {
  const name = process.argv[2] || 'happy';
  await withChannel(async (ch) => {
    if (name === 'all') {
      for (const s of ['happy', 'dedup', 'latency', 'concurrency', 'failure', 'poison', 'safety', 'evaluation', 'learning']) {
        await scenarios[s](ch);
        await sleep(2000);
      }
    } else if (scenarios[name]) {
      await scenarios[name](ch);
    } else {
      console.error(`unknown scenario "${name}". Available: ${Object.keys(scenarios).join(', ')}, all`);
      process.exitCode = 1;
    }
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

