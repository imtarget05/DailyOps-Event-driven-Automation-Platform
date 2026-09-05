'use strict';

const client = require('prom-client');

const registry = new client.Registry();
registry.setDefaultLabels({ app: 'dailyops' });

const c = (name, help, labels) =>
  new client.Counter({ name, help, labelNames: labels || [], registers: [registry] });
const h = (name, help, labels, buckets) =>
  new client.Histogram({
    name,
    help,
    labelNames: labels || [],
    registers: [registry],
    buckets: buckets || [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300],
  });

const metrics = {
  // -- events
  eventsReceived: c('dailyops_events_received_total', 'Events consumed from the broker', ['source', 'event_type']),
  eventsInvalid: c('dailyops_events_invalid_total', 'Events rejected by validation'),
  eventsDeduplicated: c('dailyops_events_deduplicated_total', 'Duplicate events dropped by deduplication window'),
  eventsIdempotentSkipped: c('dailyops_events_idempotent_skipped_total', 'Events skipped because event_id was already processed'),
  eventsRedacted: c('dailyops_events_redacted_fields_total', 'Sensitive fields redacted from event payloads'),

  // -- dispatch / processing
  eventsDispatched: c('dailyops_events_dispatched_total', 'Events dispatched to n8n webhooks', ['workflow']),
  eventsProcessed: c('dailyops_events_processed_total', 'Events processed to a terminal status (from result events)', ['workflow', 'status']),
  eventsFailed: c('dailyops_events_failed_total', 'Events that ended in failure', ['workflow']),
  retryTotal: c('dailyops_retry_total', 'Dispatch retry attempts', ['workflow']),
  deadletterTotal: c('dailyops_deadletter_total', 'Events moved to the dead-letter queue', ['reason']),

  // -- latency (seconds)
  eventToActionLatency: h('dailyops_event_to_action_latency_seconds', 'event timestamp -> result event received', ['event_type']),
  workflowDuration: h('dailyops_workflow_duration_seconds', 'dispatch -> result event received', ['workflow', 'status']),
  dispatchLatency: h('dailyops_dispatch_latency_seconds', 'gateway -> n8n webhook response (workflow startup)', ['workflow']),
  brokerLatency: h('dailyops_broker_latency_seconds', 'event publish -> gateway consume', ['source']),

  // -- gauges
  activeExecutions: new client.Gauge({
    name: 'dailyops_active_executions',
    help: 'In-flight concurrent n8n executions',
    labelNames: ['workflow'],
    registers: [registry],
  }),
  queueDepth: new client.Gauge({
    name: 'dailyops_queue_depth',
    help: 'Messages waiting in a broker queue',
    labelNames: ['queue'],
    registers: [registry],
  }),

  // -- business outcomes
  notificationsSent: c('dailyops_notifications_sent_total', 'Notifications delivered', ['channel']),
  notificationsSkipped: c('dailyops_notifications_skipped_total', 'Notifications skipped (channel not configured)'),
  ticketsCreated: c('dailyops_tickets_created_total', 'Incident tickets created', ['backend']),
};

module.exports = { registry, metrics };
