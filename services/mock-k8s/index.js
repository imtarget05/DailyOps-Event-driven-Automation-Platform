'use strict';

/**
 * Mock Kubernetes API for local demos. In production the Auto-fix workflow's
 * HTTP nodes point at a real Kubernetes API (service-account token) instead.
 *
 * Pod behaviours:
 *   api-7d9f   crashloop -> restart -> healthy after ~3s (happy auto-fix)
 *   flaky-pod  first restart returns 503 (transient error -> retry demo)
 *   bad-pod    restarts fine but health NEVER becomes healthy (permanent
 *              failure -> auto_fix.failed -> ticket + escalation demo)
 */
const express = require('express');
const app = express();
app.use(express.json());

const pods = new Map(
  [
    ['api-7d9f', { permanent: false, flaky: false }],
    ['worker-2', { status: 'running', permanent: false, flaky: false }],
    ['flaky-pod', { permanent: false, flaky: true }],
    ['bad-pod', { permanent: true, flaky: false }],
  ].map(([name, cfg]) => [
    name,
    {
      name,
      namespace: 'production',
      status: cfg.status || 'crashloop',
      restarts: 0,
      flaky: cfg.flaky,
      permanent: cfg.permanent,
      last_restart: null,
    },
  ])
);

const fail = (res, code, msg) => res.status(code).json({ error: msg });

// POST /api/v1/pods/:ns/:pod/restart
app.post('/api/v1/pods/:ns/:pod/restart', (req, res) => {
  const pod = pods.get(req.params.pod);
  if (!pod) return fail(res, 404, `pod ${req.params.pod} not found`);
  if (pod.flaky && pod.restarts === 0) {
    pod.restarts += 1;
    return fail(res, 503, 'simulated transient failure: kubelet timeout');
  }
  pod.restarts += 1;
  pod.last_restart = new Date().toISOString();
  pod.status = 'restarting';
  setTimeout(() => {
    if (!pod.permanent) pod.status = 'running';
  }, 3000);
  res.json({ pod: pod.name, namespace: pod.namespace, status: 'restarting', restarts: pod.restarts });
});

// GET /api/v1/pods/:ns/:pod/health
app.get('/api/v1/pods/:ns/:pod/health', (req, res) => {
  const pod = pods.get(req.params.pod);
  if (!pod) return fail(res, 404, `pod ${req.params.pod} not found`);
  const healthy = pod.status === 'running';
  res.json({ pod: pod.name, namespace: pod.namespace, status: healthy ? 'healthy' : pod.status, ready: healthy });
});

// GET /api/v1/pods
app.get('/api/v1/pods', (_req, res) => {
  res.json({ items: [...pods.values()].map(({ flaky, permanent, ...p }) => ({ ...p, flaky, permanent })) });
});

app.listen(9100, () => console.log('[mock-k8s] listening on :9100'));
