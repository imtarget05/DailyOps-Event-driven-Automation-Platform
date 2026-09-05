'use strict';

/**
 * Small KV/list store. Uses Redis when REDIS_URL points at a server,
 * falls back to in-memory Maps (single-process dev / tests) otherwise.
 */
class Store {
  constructor() {
    this.client = null;
    this.mem = new Map(); // key -> {v, expiresAt}
    this.lists = new Map(); // list name -> array
    this.counters = new Map();
  }

  async connect(url) {
    if (!url || url === 'memory') return;
    const redis = require('redis');
    this.client = redis.createClient({ url });
    this.client.on('error', (e) => console.error('[store] redis error:', e.message));
    await this.client.connect();
    console.log('[store] connected to redis');
  }

  _memSweep() {
    const now = Date.now();
    for (const [k, e] of this.mem) if (e.expiresAt && e.expiresAt < now) this.mem.delete(k);
  }

  /** SET key value NX EX ttl -> returns truthy if acquired */
  async setnx(key, value, ttlS) {
    if (this.client) {
      try {
        return await this.client.set(key, value, { NX: true, EX: ttlS });
      } catch (e) {
        console.error('[store] redis setnx failed, using memory:', e.message);
      }
    }
    this._memSweep();
    if (this.mem.has(key)) return null;
    this.mem.set(key, { v: value, expiresAt: Date.now() + ttlS * 1000 });
    return 'OK';
  }

  async set(key, value, ttlS) {
    if (this.client) {
      try {
        await this.client.set(key, value, ttlS ? { EX: ttlS } : undefined);
        return;
      } catch (e) {
        console.error('[store] redis set failed:', e.message);
      }
    }
    this.mem.set(key, { v: value, expiresAt: ttlS ? Date.now() + ttlS * 1000 : null });
  }

  async get(key) {
    if (this.client) {
      try {
        const v = await this.client.get(key);
        if (v !== null) return v;
      } catch (e) {
        /* fall through to memory */
      }
    }
    this._memSweep();
    const e = this.mem.get(key);
    return e ? e.v : null;
  }

  async incr(key) {
    if (this.client) {
      try {
        return await this.client.incr(key);
      } catch (e) {
        /* fall through */
      }
    }
    const n = (this.counters.get(key) || 0) + 1;
    this.counters.set(key, n);
    return n;
  }

  async rpush(list, value) {
    if (this.client) {
      try {
        await this.client.rPush(list, value);
        return;
      } catch (e) {
        /* fall through */
      }
    }
    if (!this.lists.has(list)) this.lists.set(list, []);
    this.lists.get(list).push(value);
  }

  async lrange(list, start, stop) {
    if (this.client) {
      try {
        return await this.client.lRange(list, start, stop);
      } catch (e) {
        /* fall through */
      }
    }
    const arr = this.lists.get(list) || [];
    if (stop === -1) return arr.slice(start);
    return arr.slice(start, stop + 1);
  }

  async close() {
    if (this.client) await this.client.quit().catch(() => {});
  }
}

module.exports = { Store };
