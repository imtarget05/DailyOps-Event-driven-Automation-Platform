'use strict';

const SENSITIVE_KEY = /(pass(word)?|secret|token|api[-_]?key|authorization|credential|private[-_]?key)/i;
const MAX_EVENT_BYTES = 256 * 1024;

/** Returns an error string if the event is invalid, else null. */
function validate(ev) {
  if (!ev || typeof ev !== 'object' || Array.isArray(ev)) return 'event must be a JSON object';
  for (const f of ['event_type', 'source', 'event_id', 'timestamp']) {
    if (typeof ev[f] !== 'string' || !ev[f]) return `missing or invalid field: ${f}`;
  }
  if (Number.isNaN(Date.parse(ev.timestamp))) return 'timestamp is not a valid date';
  if (ev.payload !== undefined && (typeof ev.payload !== 'object' || Array.isArray(ev.payload) === typeof ev.payload)) {
    if (typeof ev.payload !== 'object') return 'payload must be an object';
  }
  let size;
  try {
    size = Buffer.byteLength(JSON.stringify(ev));
  } catch {
    return 'event is not serializable';
  }
  if (size > MAX_EVENT_BYTES) return `event too large (${size} bytes)`;
  return null;
}

/** Recursively redacts sensitive keys inside payload. Returns count of redacted fields. */
function redact(ev) {
  if (!ev || typeof ev.payload !== 'object' || !ev.payload) return 0;
  let count = 0;
  const walk = (obj) => {
    if (Array.isArray(obj)) {
      obj.forEach(walk);
      return;
    }
    if (obj && typeof obj === 'object') {
      for (const k of Object.keys(obj)) {
        if (SENSITIVE_KEY.test(k)) {
          obj[k] = '[REDACTED]';
          count += 1;
        } else {
          walk(obj[k]);
        }
      }
    }
  };
  walk(ev.payload);
  return count;
}

module.exports = { validate, redact };
