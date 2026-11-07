import test from 'node:test';
import assert from 'node:assert/strict';
import { aggregateSessions, isNextEnd, wallTime } from '../src/aggregate.mjs';
import { advance, minimalFeatures } from '../src/engine.mjs';
import { consumeBars, emptyRecord, SCHEMA_VERSION } from '../src/state.mjs';
import { adjustmentFactors, assetTick, mergeMinuteResponse } from '../src/host_data.mjs';
import { scenario, SCENARIOS, toMinuteSessions } from './cases.mjs';

const options = (c, more = {}) => ({ ...c, symbol: '600000.SH', seenAt: '2026-11-07 18:00:00', ...more });
const run = (c, previous = null, more = {}) => consumeBars(previous, c.bars, options(c, more));

test('at least 56 real completed bars, never a stored moving-average shortcut', () => {
  const c = scenario(SCENARIOS.single, 54), result = run(c);
  assert.equal(result.record.state.decision.veto, 'WARMUP');
  assert.equal(result.record.confirmations.length, 0);
  assert.ok(Number.isNaN(minimalFeatures([], { close: 100, high: 101, low: 99, volume: 100, ma55: 100 }, []).known_ma55));
  assert.ok(Number.isFinite(run(scenario([], 56)).record.state.decision.known_ma55));
});
test('same-clock baseline requires 3 earlier sessions and remembers at most 5', () => {
  const bar = { timestamp: '2026-11-06 09:45:00', open: 100, high: 102, low: 99, close: 101, volume: 150 };
  assert.ok(Number.isNaN(minimalFeatures([], bar, [50, 50]).vol_slot));
  assert.equal(minimalFeatures([], bar, [50, 50, 50]).vol_slot, 3);
  const record = run(scenario([], 240)).record;
  assert.equal(record.state.feature_history.length, 55);
  assert.ok(Object.values(record.state.slot_volumes).every(v => v.length <= 5));
});
for (const [name, expected] of [['single', ['SINGLE_BAR']], ['duplicate', ['SINGLE_BAR']],
  ['departure', ['SINGLE_BAR', 'MULTI_BAR']], ['deeper', ['SINGLE_BAR', 'MULTI_BAR']],
  ['recovery', ['MULTI_BAR']], ['fresh', ['SINGLE_BAR', 'SINGLE_BAR']]]) {
  test(`${name}: frozen support-swing confirmations`, () => {
    assert.deepEqual(run(scenario(SCENARIOS[name])).record.confirmations.map(e => e.pattern), expected);
  });
}
test('new lower foot waits for a later red, and 4-bar pending lifetime expires', () => {
  const c = scenario(SCENARIOS.deeper.slice(0, 4));
  assert.equal(run(c).record.state.reason, 'NEW_LOWER_FOOT');
  const base = { timestamp: '2026-11-06 10:00:00', open: 103, high: 104, low: 102,
    close: 103, volume: 100, known_ma55: 100, ma55: 100, adjust_factor: 1,
    vol20: 1, vol_slot: NaN, range16: .03 };
  const previous = { index: 8, last_impulse: { index: 8 }, pending: { index: 5, at: '2026-11-06 09:45:00', low: 99 },
    locked: false, last_buy: null, saw_downward_retest: false, previous_at: '2026-11-06 09:45:00', previous_close: 103 };
  const kept = advance(previous, base);
  assert.equal(kept.structure.pending.index, 5);
  assert.equal(advance(kept.structure, { ...base, timestamp: '2026-11-06 10:15:00' }).structure.pending, null);
});
test('unconfirmed tests clear overnight, confirmed swing locks survive overnight', () => {
  const b = { timestamp: '2026-11-06 09:45:00', open: 103, high: 104, low: 102, close: 103, volume: 100,
    known_ma55: 100, ma55: 103, adjust_factor: 1, vol20: 1, vol_slot: NaN, range16: .03 };
  const previous = { index: 9, last_impulse: { index: 9 }, pending: { index: 9, at: '2026-11-05 15:00:00', low: 99 },
    locked: false, last_buy: null, saw_downward_retest: false, previous_at: '2026-11-05 15:00:00', previous_close: 103 };
  assert.equal(advance(previous, b).structure.pending, null);
  previous.locked = true; previous.pending = null; previous.last_buy = { index: 9, low: 99 };
  assert.equal(advance(previous, b).decision.veto, 'SAME_SUPPORT_SWING');
});
test('stock and ETF ticks differ, and adjusted tick controls two-tick red', () => {
  assert.equal(assetTick('stock'), .01); assert.equal(assetTick('etf'), .001); assert.equal(assetTick('index'), null);
  const bar = { timestamp: '2026-11-06 11:15:00', open: .5, high: .51, low: .498, close: .5041,
    volume: 500, known_ma55: .5, ma55: .5001, adjust_factor: 1, vol20: 2, vol_slot: NaN, range16: .03 };
  assert.equal(advance(null, bar, .001).decision.event, 'BUY_STRUCTURE');
  assert.equal(advance(null, bar, .01).decision.event, '');
  assert.equal(advance(null, { ...bar, adjust_factor: 3 }, .001).decision.event, '');
});
test('1m→3 real 5m→15m preserves OHLCV and excludes auction/lunch quotes', () => {
  const c = scenario(SCENARIOS.single, 64), sessions = toMinuteSessions(c.bars);
  const auction = { ...sessions[0].rows[0], datetime: `${sessions[0].date}T09:30:00`, high: 999, volume: 999999 };
  sessions[0].rows.push(auction, { ...auction, datetime: `${sessions[0].date}T13:00:00` });
  const result = aggregateSessions(sessions, '2026-12-01 16:00:00');
  for (let i = 0; i < c.bars.length; i++) {
    for (const key of ['open', 'high', 'low', 'close', 'volume']) assert.equal(result.fifteen[i][key], c.bars[i][key]);
    assert.equal(result.fifteen[i].source_count, 3);
  }
  assert.equal(result.five.filter(r => r.timestamp.slice(11) === '09:35:00').length, sessions.length);
  assert.ok(isNextEnd('2026-11-06 11:30:00', '2026-11-06 13:15:00', ['2026-11-06']));
  assert.ok(isNextEnd('2026-11-06 15:00:00', '2026-11-09 09:45:00', ['2026-11-06', '2026-11-09']));
});
for (const [name, mutate, reason] of [
  ['missing minute', s => s[0].rows.splice(3, 1), 'MISSING_MINUTE'],
  ['null open', s => { s[0].rows[3].open = null; }, 'INVALID_OR_NULL_OPEN'],
  ['dirty OHLC', s => { s[0].rows[3].high = 1; }, 'INVALID_OR_NULL_OPEN'],
  ['conflicting duplicate', s => s[0].rows.push({ ...s[0].rows[3], volume: 88 }), 'CONFLICTING_MINUTE'],
]) test(`${name} blocks a formal 15m input`, () => {
  const sessions = toMinuteSessions(scenario([], 16).bars); mutate(sessions);
  assert.equal(aggregateSessions(sessions, '2026-11-07 18:00:00').fifteen[0].reason, reason);
});
test('Beijing naive times, unfinished last minute, 13:33 cutoff and final 15:00', () => {
  assert.equal(wallTime('2026-11-06T09:31:00+08:00'), '2026-11-06 09:31:00');
  assert.throws(() => wallTime('2026-11-06T01:31:00Z'));
  const c = scenario([], 16), day = c.bars[0].timestamp.slice(0, 10), sessions = toMinuteSessions(c.bars);
  assert.equal(aggregateSessions(sessions, `${day} 09:45:59`).fifteen.length, 0);
  assert.equal(aggregateSessions(sessions, `${day} 09:46:00`).fifteen.at(-1).timestamp, `${day} 09:45:00`);
  assert.equal(aggregateSessions(sessions, `${day} 13:33:00`).fifteen.at(-1).timestamp, `${day} 13:30:00`);
  assert.equal(aggregateSessions(sessions, `${day} 15:01:00`).fifteen.length, 16);
});
test('exact duplicate inputs, JSON restart and rolling windows preserve event identity', () => {
  const c = scenario(SCENARIOS.departure); const full = run(c).record;
  let record = null;
  for (let n = 1; n <= c.bars.length; n++) record = run({ ...c, bars: c.bars.slice(Math.max(0, n - 64), n) },
    record && JSON.parse(JSON.stringify(record))).record;
  assert.deepEqual(record.confirmations, full.confirmations);
  const repeated = run(c, record);
  assert.deepEqual(repeated.added, []); assert.equal(repeated.record.state.processed_at, full.state.processed_at);
  assert.equal(repeated.record.state.structure.index, full.state.structure.index);
});
test('gaps, missing factors and price-basis changes require a new 56-bar run', () => {
  const c = scenario(SCENARIOS.single, 128), first = run(c).record;
  for (const kind of ['gap', 'factor', 'scale']) {
    const next = scenario([], 220); next.bars = next.bars.slice(129);
    if (kind === 'gap') next.bars.shift();
    if (kind === 'factor') next.factors = {};
    if (kind === 'scale') next.factors = Object.fromEntries(next.sessions.map(d => [d, .5]));
    const result = run({ ...next, bars: next.bars.slice(0, 40) }, first).record;
    assert.ok(result.state.valid_run < 56); assert.equal(result.confirmations.length, first.confirmations.length);
  }
});
test('immutable first confirmation, separate risk, replay/live/delayed and version isolation', () => {
  const c = scenario(SCENARIOS.single), at = c.bars.at(-1).timestamp;
  const first = run(c, null, { origin: 'LIVE', seenAt: at.slice(0, 16) + ':59', liveAfter: '2026-09-01 08:00:00' }).record;
  assert.equal(first.confirmations[0].mode, 'LIVE');
  assert.equal(run(c, null, { origin: 'LIVE', seenAt: at.slice(0, 10) + ' 10:00:00',
    liveAfter: at.slice(0, 10) + ' 08:00:00' }).record.confirmations[0].mode, 'DELAYED');
  assert.equal(run(c, null, { origin: 'LIVE', seenAt: at.slice(0, 16) + ':59' }).record.confirmations[0].mode, 'REPLAY');
  const next = scenario([...SCENARIOS.single, [101, 101, 96, 97, 90_000]]);
  const result = run(next, first).record;
  assert.deepEqual(result.confirmations[0], first.confirmations[0]);
  assert.ok(result.risks[first.confirmations[0].event_id].invalidated_at);
  const old = { ...first, rule_version: 'MA55_V4' };
  assert.equal(run(scenario([], 16), old).record.confirmations.length, 0);
  assert.equal(emptyRecord('s').schema_version, SCHEMA_VERSION);
});
test('host lots and reference shares give the same rule decisions and event identities', () => {
  const c = scenario(SCENARIOS.departure);
  const scaled = { ...c, bars: c.bars.map(b => ({ ...b, volume: b.volume / 100 })) };
  const first = run(c).record, second = run(scaled).record;
  assert.deepEqual(first.confirmations.map(e => e.event_id), second.confirmations.map(e => e.event_id));
  assert.equal(first.state.decision.volume_ratio20, second.state.decision.volume_ratio20);
});
test('an old frozen defense is not compared to prices after its adjustment basis changes', () => {
  const first = run(scenario(SCENARIOS.single)).record, c = scenario([], 130);
  const next = { ...c.bars[129], open: 50, high: 50.1, low: 49.9, close: 50 };
  const factors = Object.fromEntries(c.sessions.map(d => [d, .5]));
  const result = run({ ...c, bars: [next], factors }, first).record;
  assert.deepEqual(result.confirmations[0], first.confirmations[0]);
  const risk = result.risks[first.confirmations[0].event_id];
  assert.equal(risk.reason, 'PRICE_BASIS_CHANGED'); assert.equal(risk.invalidated_at, null);
});
test('adjustment ratio only comes from matching symbol/day/asset and verifiable close basis', () => {
  const range = { symbol: '588170.SH', asset_type: 'etf', sessions: [{ date: '2026-11-06',
    rows: [{ datetime: '2026-11-06T15:00:00', close: 1 }] }] };
  const daily = { symbol: range.symbol, asset_type: 'etf', source: 'enriched',
    rows: [{ symbol: range.symbol, date: '2026-11-06', close: 1, raw_close: 2 }] };
  assert.equal(adjustmentFactors(range.symbol, range, daily, '2026-11-07 15:00:00').factors['2026-11-06'], .5);
  for (const altered of [{ ...daily, symbol: '600000.SH' }, { ...daily, asset_type: 'stock' },
    { ...daily, rows: [{ ...daily.rows[0], raw_close: null }] },
    { ...daily, rows: [{ ...daily.rows[0], date: '2026-11-05' }] },
    { ...daily, rows: [{ ...daily.rows[0], close: 1.1 }] }]) {
    assert.deepEqual(adjustmentFactors(range.symbol, range, altered, '2026-11-07 15:00:00').factors, {});
  }
});
test('batch incremental/full/empty/truncated semantics and dynamic last-minute overwrite', () => {
  const day = '2026-11-06', old = [{ datetime: `${day}T09:31:00`, close: 1 }];
  const newer = { datetime: `${day}T09:31:00`, close: 2 };
  assert.equal(mergeMinuteResponse('s', old, { data: { s: [newer] }, incremental: true }, day).rows[0].close, 2);
  assert.deepEqual(mergeMinuteResponse('s', old, { data: {}, incremental: true }, day).rows, old);
  assert.equal(mergeMinuteResponse('s', old, { data: {} }, day).blocked, 'MINUTE_UNAVAILABLE');
  assert.equal(mergeMinuteResponse('s', old, { data: { s: [newer] }, truncated: ['s'] }, day).blocked, 'MINUTE_TRUNCATED');
  assert.equal(mergeMinuteResponse('s', old, { data: { s: [{ ...newer, datetime: '2026-11-05T09:31:00' }] } }, day).blocked, 'WRONG_TRADING_DATE');
});
