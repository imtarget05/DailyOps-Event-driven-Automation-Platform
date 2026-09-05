'use strict';

const config = require('./config');

/**
 * Declares the full broker topology (idempotent):
 *
 *   dailyops.events (topic)
 *     service.alert      -> q.n8n.autofix, q.n8n.notify, q.n8n.ticket
 *     task.completed       -> q.n8n.notify
 *     document.indexed     -> q.n8n.notify
 *     auto_fix.completed   -> q.n8n.notify
 *     auto_fix.failed      -> q.n8n.notify, q.n8n.ticket
 *     auto_fix.skipped     -> q.n8n.notify
 *     incident.ticket_created -> q.n8n.notify
 *     system.workflow_failed  -> q.n8n.notify
 *     system.deadletter       -> q.n8n.notify
 *     (result events like notify.completed are intentionally unbound)
 *
 *   dailyops.dlx (topic) <- dead-letters from every q.n8n.*
 *     #                    -> q.dlq.dailyops
 */
const BINDINGS = {
  'q.n8n.autofix': ['service.alert'],
  'q.n8n.notify': [
    'service.alert',
    'task.completed',
    'document.indexed',
    'auto_fix.completed',
    'auto_fix.failed',
    'auto_fix.skipped',
    'incident.ticket_created',
    'system.workflow_failed',
    'system.deadletter',
  ],
  'q.n8n.ticket': ['service.alert', 'auto_fix.failed', 'system.deadletter'],
  'q.dlq.dailyops': ['#'],
};

const DLQ_ARGS = { 'x-dead-letter-exchange': config.dlx, 'x-dead-letter-routing-key': 'dropped' };

async function assertTopology(ch, log) {
  await ch.assertExchange(config.exchange, 'topic', { durable: true });
  await ch.assertExchange(config.dlx, 'topic', { durable: true });

  for (const [queue, keys] of Object.entries(BINDINGS)) {
    const args = queue === config.dlq ? {} : DLQ_ARGS;
    await ch.assertQueue(queue, { durable: true, arguments: args });
    for (const rk of keys) {
      const exchange = queue === config.dlq ? config.dlx : config.exchange;
      await ch.bindQueue(queue, exchange, rk);
    }
    log(`queue ${queue} ready (${keys.join(', ')})`);
  }
}

module.exports = { assertTopology, BINDINGS };
