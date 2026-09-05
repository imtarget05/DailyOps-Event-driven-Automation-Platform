'use strict';

function env(name, def) {
  const v = process.env[name];
  return v === undefined || v === '' ? def : v;
}

module.exports = {
  port: Number(env('GATEWAY_PORT', 9090)),

  rabbitUrl: env('RABBITMQ_URL', ''),
  rabbitMgmtUrl: env('RABBITMQ_MGMT_URL', ''),
  rabbitMgmtUser: env('RABBITMQ_MGMT_USER', 'dailyops'),
  rabbitMgmtPass: env('RABBITMQ_MGMT_PASS', 'dailyops'),
  // 'memory' (or empty) runs the gateway without RabbitMQ/Redis (dev/tests, HTTP API only)
  redisUrl: env('REDIS_URL', 'memory'),

  n8nBaseUrl: env('N8N_BASE_URL', 'http://localhost:5678'),

  // RabbitMQ prefetch == max concurrent in-flight n8n dispatches
  maxConcurrency: Number(env('MAX_CONCURRENCY', 8)),
  suppressionWindowS: Number(env('SUPPRESSION_WINDOW_S', 300)),
  retryMax: Number(env('RETRY_MAX', 3)),
  retryBackoffMs: Number(env('RETRY_BACKOFF_MS', 500)),
  dispatchTimeoutMs: Number(env('DISPATCH_TIMEOUT_MS', 15000)),

  telegramBotToken: env('TELEGRAM_BOT_TOKEN', ''),
  telegramChatId: env('TELEGRAM_CHAT_ID', ''),
  smtp: {
    host: env('SMTP_HOST', ''),
    port: Number(env('SMTP_PORT', 587)),
    user: env('SMTP_USER', ''),
    pass: env('SMTP_PASS', ''),
    from: env('SMTP_FROM', 'dailyops@example.com'),
    to: env('SMTP_TO', 'oncall@example.com'),
  },
  jira: {
    baseUrl: env('JIRA_BASE_URL', ''),
    email: env('JIRA_EMAIL', ''),
    token: env('JIRA_API_TOKEN', ''),
    projectKey: env('JIRA_PROJECT_KEY', 'DOPS'),
  },

  // queue -> n8n webhook path + logical workflow name
  queues: {
    'q.n8n.autofix': { workflow: 'auto-fix', webhook: '/webhook/auto-fix' },
    'q.n8n.notify': { workflow: 'notify', webhook: '/webhook/notify' },
    'q.n8n.ticket': { workflow: 'incident-ticket', webhook: '/webhook/incident-ticket' },
  },
  exchange: 'dailyops.events',
  dlx: 'dailyops.dlx',
  dlq: 'q.dlq.dailyops',
};
