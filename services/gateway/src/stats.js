'use strict';

/**
 * In-memory ring buffer of per-event latency samples + in-flight tracking.
 * Powers GET /stats (used by the simulator and load test for quantiles).
 */
class Stats {
  constructor(cap = 2000) {
    this.cap = cap;
    this.recent = [];
    this.inflight = new Map(); // event_id -> {event_type, workflow, timestamp, dispatchedAt}
  }

  key(workflow, eventId) {
    return `${workflow}:${eventId}`;
  }

  begin(event, workflow) {
    this.inflight.set(this.key(workflow, event.event_id), {
      event_id: event.event_id,
      event_type: event.event_type,
      source: event.source,
      workflow,
      timestamp: Date.parse(event.timestamp),
      dispatchedAt: null,
    });
    // hard TTL: sweep entries older than 5 minutes
    const cutoff = Date.now() - 5 * 60 * 1000;
    for (const [id, e] of this.inflight) if (e.timestamp < cutoff) this.inflight.delete(id);
  }

  markDispatched(eventId, workflow) {
    const e = this.inflight.get(this.key(workflow, eventId));
    if (e) e.dispatchedAt = Date.now();
  }

  end(eventId, workflow) {
    const e = this.inflight.get(workflow ? this.key(workflow, eventId) : eventId);
    this.inflight.delete(eventId);
    return e;
  }

  record(sample) {
    sample.t = Date.now();
    this.recent.push(sample);
    if (this.recent.length > this.cap) this.recent.splice(0, this.recent.length - this.cap);
  }

  json() {
    return {
      inflight: [...this.inflight.values()].map((e) => ({
        event_id: e.event_id,
        event_type: e.event_type,
        workflow: e.workflow,
        ms_since_event: e.timestamp ? Date.now() - e.timestamp : null,
      })),
      recent: this.recent.slice(-500),
    };
  }
}

module.exports = { Stats };
