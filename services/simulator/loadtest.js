'use strict';

/**
 * Load test: publishes N alert events at a fixed rate, then reports
 * event-to-action latency quantiles + counters from the gateway.
 *
 *   node loadtest.js --events 200 --rate 25
 *
 * Writes artifacts/loadtest-report.json (mounted at /artifacts in compose).
 */
const amqp = require('amqplib');
const { serviceAlert, sleep, fetchJson, fetchMetricsText, summarize, fmt } = require('./events');

const GATEWAY = process.env.GATEWAY_URL || 'http://localhost:9090';

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? Number(process.argv[i + 1]) : def;
}

function counter(text, name) {
  return Number(text.match(new RegExp(`^${name}(?:\\{[^}]*\\})? (\\d+)`, 'm'))?.[1] || 0);
}

async function main() {
  const events = arg('events', 100);
  const rate = arg('rate', 25); // events per second
  const conn = await amqp.connect(process.env.AMQP_URL || '');
  const ch = await conn.createChannel();
  await ch.assertExchange('dailyops.events', 'topic', { durable: true });

  console.log(`load test: ${events} events @ ${rate}/s (each alert fans out to 3 queues)`);
  const t0 = Date.now();
  for (let i = 0; i < events; i++) {
    const ev = serviceAlert({ pod: 'api-7d9f', dedup_key: `loadtest:${t0}:${i}` });
    ch.publish('dailyops.events', ev.event_type, Buffer.from(JSON.stringify(ev)), {
      contentType: 'application/json',
      persistent: true,
    });
    if ((i + 1) % 10 === 0) process.stdout.write(`\rpublished ${i + 1}/${events}`);
    if (rate > 0) await sleep(1000 / rate);
  }
  console.log(`\rpublished ${events}/${events} — waiting for results...`);

  // wait for all tracked events to reach a terminal state
  let stats;
  const deadline = Date.now() + 300_000;
  for (;;) {
    await sleep(2000);
    stats = await fetchJson(`${GATEWAY}/stats`);
    const done = stats.recent.filter((s) => s.total_ms && s.workflow === 'auto-fix').length;
    if (done >= events || Date.now() > deadline) break;
  }
  await conn.close();

  const m = await fetchMetricsText(`${GATEWAY}/metrics`);
  const rows = stats.recent.filter((s) => s.total_ms && s.workflow === 'auto-fix');
  const report = {
    generated_at: new Date().toISOString(),
    config: { events, rate },
    duration_s: Math.round((Date.now() - t0) / 1000),
    counters: {
      events_received: counter(m, 'dailyops_events_received_total'),
      events_dispatched: counter(m, 'dailyops_events_dispatched_total'),
      events_processed: counter(m, 'dailyops_events_processed_total'),
      events_failed: counter(m, 'dailyops_events_failed_total'),
      deduplicated: counter(m, 'dailyops_events_deduplicated_total'),
      idempotent_skipped: counter(m, 'dailyops_events_idempotent_skipped_total'),
      retries: counter(m, 'dailyops_retry_total'),
      deadletter: counter(m, 'dailyops_deadletter_total'),
    },
    event_to_action_latency_ms: summarize(rows.map((s) => s.total_ms)),
    workflow_duration_ms: summarize(rows.map((s) => s.workflow_ms).filter(Number.isFinite)),
    dispatch_startup_ms: summarize(rows.map((s) => s.dispatch_ms).filter(Number.isFinite)),
    samples: rows.map((s) => ({ event_id: s.event_id, total_ms: s.total_ms, workflow_ms: s.workflow_ms })),
  };

  console.log('\n--------- load test report ---------');
  console.log(`duration: ${report.duration_s}s`);
  console.log('counters:', JSON.stringify(report.counters));
  const lat = report.event_to_action_latency_ms;
  console.log(`event-to-action: p50=${fmt(lat.p50)} p95=${fmt(lat.p95)} p99=${fmt(lat.p99)} max=${fmt(lat.max)} (n=${lat.n})`);
  console.log(`workflow action: p50=${fmt(report.workflow_duration_ms.p50)} p95=${fmt(report.workflow_duration_ms.p95)}`);

  const fs = require('fs');
  const out = process.env.REPORT_PATH || '/artifacts/loadtest-report.json';
  try {
    fs.mkdirSync(require('path').dirname(out), { recursive: true });
    fs.writeFileSync(out, JSON.stringify(report, null, 2));
    console.log(`report written to ${out}`);
  } catch (e) {
    console.warn('could not write report file:', e.message);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
