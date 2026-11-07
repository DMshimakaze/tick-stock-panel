import { RULE_VERSION, minimalFeatures, advance } from './engine.mjs';
import { isNextEnd, wallMillis } from './aggregate.mjs';

export const SCHEMA_VERSION = 1;
export const EVENT_LIMIT = 120;
const MAX_SYMBOLS = 80;
export function emptyRecord(symbol) {
  return { symbol, schema_version: SCHEMA_VERSION, rule_version: RULE_VERSION,
    state: { processed_at: null, data_asof: null, valid_run: 0, last_factor: null,
      structure: null, feature_history: [], slot_volumes: {}, sequence: 0, decision: null,
      reason: 'INSUFFICIENT_WARMUP' }, confirmations: [], risks: {} };
}
function resetRun(s, reason) {
  Object.assign(s, { valid_run: 0, feature_history: [], structure: null, decision: null, reason });
}
export function consumeBars(previous, bars, options) {
  const { symbol, tick, factors, sessions, seenAt, origin = 'REPLAY', liveAfter = seenAt, factorReasons = {} } = options;
  const compatible = previous?.schema_version === SCHEMA_VERSION && previous.rule_version === RULE_VERSION;
  const record = compatible ? structuredClone(previous) : emptyRecord(symbol);
  const s = record.state, added = [];
  const savedFactor = s.processed_at && (options.dailyFactors || factors)[s.processed_at.slice(0, 10)];
  if (savedFactor && s.last_factor && Math.abs(savedFactor / s.last_factor - 1) > 1e-6) {
    for (const event of record.confirmations) record.risks[event.event_id] = {
      ...record.risks[event.event_id], data_pending_at: seenAt, reason: 'PRICE_BASIS_CHANGED' };
    resetRun(s, 'PRICE_BASIS_CHANGED');
    // The saved watermark remains monotone; new bars accumulate a fresh 56-bar run.
    s.last_factor = savedFactor;
  }
  for (const raw of bars) {
    const stamp = raw.timestamp;
    if (s.processed_at && stamp <= s.processed_at) continue;
    const factor = factors[stamp.slice(0, 10)];
    const gap = s.processed_at && !isNextEnd(s.processed_at, stamp, sessions);
    const scaleChanged = factor && s.last_factor && Math.abs(factor / s.last_factor - 1) > 1e-6;
    const reason = !sessions.includes(stamp.slice(0, 10)) ? 'TRADING_CALENDAR_UNAVAILABLE' :
      !raw.valid ? raw.reason : !tick ? 'TICK_UNAVAILABLE' : !factor ?
        (factorReasons[stamp.slice(0, 10)] || 'HISTORICAL_FACTOR_UNAVAILABLE') :
        scaleChanged ? 'PRICE_BASIS_CHANGED' : gap ? 'MISSING_COMPLETED_15M' : '';
    Object.assign(s, { processed_at: stamp, last_factor: factor || null, sequence: s.sequence + 1 });
    if (reason) {
      resetRun(s, reason);
      for (const event of record.confirmations) {
        const prior = record.risks[event.event_id] || {};
        record.risks[event.event_id] = { ...prior, data_pending_at: stamp,
          reason: prior.reason === 'PRICE_BASIS_CHANGED' ? prior.reason : reason };
      }
      continue;
    }
    const sample = { timestamp: stamp, open: raw.open, high: raw.high, low: raw.low,
      close: raw.close, volume: raw.volume, adjust_factor: factor };
    const slot = stamp.slice(11, 16), volumes = s.slot_volumes[slot] || [];
    const b = minimalFeatures(s.feature_history, sample, volumes);
    const { structure, decision } = advance(s.structure, b, tick);
    Object.assign(s, { structure, decision, valid_run: s.valid_run + 1,
      data_asof: stamp, reason: decision.veto || decision.event,
      feature_history: [...s.feature_history, sample].slice(-55),
      close: b.close, ma55: Number.isFinite(b.ma55) ? b.ma55 : null });
    s.slot_volumes[slot] = [...volumes, b.volume].slice(-5);
    for (const event of record.confirmations) {
      const prior = record.risks[event.event_id] || {};
      if (prior.reason === 'PRICE_BASIS_CHANGED') {
        // Frozen defense prices belong to their original basis, not today's rebased prices.
        record.risks[event.event_id] = { ...prior, age_bars: s.sequence - event.sequence };
        continue;
      }
      record.risks[event.event_id] = { ...prior, data_asof: stamp,
        data_pending_at: s.valid_run >= 56 ? null : prior.data_pending_at || null,
        invalidated_at: prior.invalidated_at || (b.close < event.defense ? stamp : null),
        age_bars: s.sequence - event.sequence,
        extended: decision.close_distance_pct > 3 };
    }
    if (decision.event) {
      const eventId = `${RULE_VERSION}:${symbol}:SUPPORT:${decision.support_test_time.replace(' ', 'T')}:TURN:${stamp.replace(' ', 'T')}`;
      if (!record.confirmations.some(e => e.event_id === eventId)) {
        const elapsed = wallMillis(seenAt) - wallMillis(stamp);
        const mode = origin === 'LIVE' && stamp > liveAfter && stamp.slice(0, 10) === seenAt.slice(0, 10) ?
          (elapsed <= 90_000 ? 'LIVE' : 'DELAYED') : 'REPLAY';
        const event = { event_id: eventId, symbol, rule_version: RULE_VERSION, event_kind: decision.event,
          setup_at: decision.support_test_time, confirmed_at: stamp, data_asof: stamp,
          first_seen_at: seenAt, mode, pattern: decision.pattern, sequence: s.sequence,
          confirm_close: b.close, defense: decision.support_test_low,
          raw_tick: tick, adjust_factor: factor, decision: structuredClone(decision),
          impulse: structuredClone(structure.last_impulse),
          risk_text: decision.close_distance_pct > 3 ? '离均线较远，不追价' : '' };
        record.confirmations.push(event);
        record.risks[eventId] = { data_asof: stamp, age_bars: 0, invalidated_at: null,
          extended: decision.close_distance_pct > 3 };
        added.push(event);
      }
    }
  }
  record.confirmations = record.confirmations.slice(-EVENT_LIMIT);
  const ids = new Set(record.confirmations.map(e => e.event_id));
  record.risks = Object.fromEntries(Object.entries(record.risks).filter(([id]) => ids.has(id)));
  record.updated_at = seenAt;
  return { record, added };
}

/** One atomic IndexedDB record owns state, frozen confirmations and separate risk.
 * Only 55 essential 15m samples and <=5 volumes/clock persist; no minute database.
 */
export class DerivedCache {
  constructor(indexedDB) { this.indexedDB = indexedDB; this.db = null; }
  async open() {
    if (!this.indexedDB) throw new Error('浏览器不支持派生状态存储；本次仍可重放，关闭后不保留');
    this.db = await new Promise((resolve, reject) => {
      const request = this.indexedDB.open('lan-ma55-v02', SCHEMA_VERSION);
      request.onupgradeneeded = () => request.result.createObjectStore('symbols', { keyPath: 'symbol' });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async get(symbol) {
    return new Promise((resolve, reject) => {
      const request = this.db.transaction('symbols').objectStore('symbols').get(symbol);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
  }
  async put(record) {
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('symbols', 'readwrite'), store = tx.objectStore('symbols');
      store.put(record);
      const request = store.getAll();
      request.onsuccess = () => {
        const rows = request.result.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
        for (const old of rows.slice(MAX_SYMBOLS)) store.delete(old.symbol);
      };
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  }
  close() { this.db?.close(); this.db = null; }
}
