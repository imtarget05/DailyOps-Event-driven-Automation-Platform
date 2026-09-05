'use strict';

const config = require('./config');

/**
 * Creates an incident ticket. Uses Jira REST when configured; otherwise a
 * durable mock store (Redis list) so the ticket workflow is demoable.
 */
async function create({ store, title, description, severity, event }) {
  const { baseUrl, email, token, projectKey } = config.jira;
  if (baseUrl) {
    const auth = Buffer.from(`${email}:${token}`).toString('base64');
    const res = await fetch(`${baseUrl.replace(/\/$/, '')}/rest/api/2/issue`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Basic ${auth}` },
      body: JSON.stringify({
        fields: {
          project: { key: projectKey },
          summary: title,
          description,
          issuetype: { name: 'Bug' },
          priority: { name: severity === 'critical' ? 'Highest' : 'High' },
          labels: ['dailyops', `severity:${severity || 'unknown'}`],
        },
      }),
    });
    if (!res.ok) throw new Error(`jira API returned ${res.status}`);
    const body = await res.json();
    return { ticket_id: body.id, key: body.key, backend: 'jira' };
  }
  const seq = await store.incr('tickets:seq');
  const ticket = {
    ticket_id: String(1000 + seq),
    key: `${projectKey}-${1000 + seq}`,
    title,
    description,
    severity: severity || 'unknown',
    event,
    created_at: new Date().toISOString(),
    backend: 'mock',
  };
  await store.rpush('tickets', JSON.stringify(ticket));
  return { ticket_id: ticket.ticket_id, key: ticket.key, backend: 'mock' };
}

module.exports = { create };
