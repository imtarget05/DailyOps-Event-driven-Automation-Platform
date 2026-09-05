'use strict';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * POST an event to an n8n webhook with transient-failure retry
 * (exponential backoff + jitter). Returns {ok, attempt, executionId, latencyMs}
 * or {ok:false, attempt, error}.
 */
async function dispatch({ url, body, workflow, metrics, retryMax, backoffMs, timeoutMs, log }) {
  const started = Date.now();
  let lastError = 'unknown';
  for (let attempt = 1; attempt <= retryMax; attempt++) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-event-id': String(body.event_id || '') },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (res.ok) {
        const text = await res.text();
        let parsed = null;
        try {
          parsed = JSON.parse(text);
        } catch {
          /* non-JSON response body is fine */
        }
        metrics.dispatchLatency.observe({ workflow }, (Date.now() - started) / 1000);
        return { ok: true, attempt, executionId: (parsed && parsed.execution_id) || null, latencyMs: Date.now() - started };
      }
      lastError = `HTTP ${res.status} from n8n`;
      if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) {
        // 4xx (except timeout/too-many-requests) is permanent: don't retry
        break;
      }
    } catch (err) {
      lastError = err.name === 'TimeoutError' ? `timeout after ${timeoutMs}ms` : String(err.message || err);
    }
    if (attempt < retryMax) {
      metrics.retryTotal.inc({ workflow });
      const wait = backoffMs * 2 ** (attempt - 1) + Math.random() * 250;
      log(`retry ${attempt}/${retryMax - 1} for event ${body.event_id} in ${Math.round(wait)}ms (${lastError})`);
      await sleep(wait);
    }
  }
  return { ok: false, attempt: retryMax, error: lastError };
}

module.exports = { dispatch, sleep };
