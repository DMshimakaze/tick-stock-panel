import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { aggregateSessions } from '../src/aggregate.mjs';
import { advance, minimalFeatures } from '../src/engine.mjs';
import { consumeBars } from '../src/state.mjs';
import { scenario, SCENARIOS, toMinuteSessions } from './cases.mjs';

const reference = process.env.MA55_REFERENCE_REPO, python = process.env.MA55_PYTHON;
function oracle(payload) {
  const run = spawnSync(python, ['-B', '-X', 'utf8', fileURLToPath(new URL('./oracle.py', import.meta.url))], {
    input: JSON.stringify(payload), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
  });
  assert.equal(run.status, 0, run.stderr || String(run.error));
  return JSON.parse(run.stdout);
}
function same(actual, expected, path = '') {
  if (typeof expected === 'number') {
    assert.ok(Number.isFinite(actual) && Math.abs(actual - expected) <= 2e-12 * Math.max(1, Math.abs(expected)), `${path}: ${actual} != ${expected}`);
  } else if (expected && typeof expected === 'object') {
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), path);
    for (const key of Object.keys(expected)) same(actual[key], expected[key], `${path}.${key}`);
  } else assert.equal(Number.isNaN(actual) ? null : actual, expected, path);
}

test('synthetic 1m→5m→15m: every decision and structure field equals pinned Python',
  { skip: !reference || !python ? 'Set MA55_REFERENCE_REPO and MA55_PYTHON for the actual Python oracle' : false }, () => {
    const cases = Object.entries(SCENARIOS).map(([name, candles]) => {
      const raw = scenario(candles);
      const bars = aggregateSessions(toMinuteSessions(raw.bars), '2026-12-01 16:00:00').fifteen
        .filter(b => b.timestamp <= raw.bars.at(-1).timestamp).map(b => ({ ...b, adjust_factor: 1 }));
      assert.ok(bars.every(b => b.valid));
      return { name, tick: raw.tick, bars };
    });
    for (const expected of oracle({ action: 'synthetic', cases })) {
      const input = cases.find(c => c.name === expected.name);
      let structure = null, history = [], slots = {};
      input.bars.forEach((bar, i) => {
        const slot = bar.timestamp.slice(11, 16), feature = minimalFeatures(history, bar, slots[slot] || []);
        const result = advance(structure, feature, input.tick);
        same(result, expected.trace[i], `${input.name}[${i}]`);
        structure = result.structure;
        history = [...history, bar].slice(-55); slots[slot] = [...slots[slot] || [], bar.volume].slice(-5);
      });
    }
  });

const pack = process.env.MA55_FROZEN_PACK;
const frozenAvailable = reference && python && pack && existsSync(join(pack, 'manifest.json'));
test('private frozen pack: actual Python analyze and Page Pack agree on 15 positive, 7 suppressed, 2 negative windows',
  { skip: frozenAvailable ? false : 'Frozen private pack or Python reference not mounted', timeout: 180_000 }, async t => {
    const cases = oracle({ action: 'frozen' });
    assert.equal(cases.length, 24);
    for (const c of cases) await t.test(`${c.kind}: ${c.symbol} ${c.when}`, () => {
      const { record } = consumeBars(null, c.bars, { symbol: c.symbol, tick: c.tick, factors: c.factors,
        sessions: c.sessions, seenAt: '2026-11-07 18:00:00' });
      same(record.state.decision, c.decision, 'decision');
      same(record.state.structure, c.structure, 'structure');
      assert.deepEqual(record.confirmations.map(e => ({ event_id: e.event_id, confirmed_at: e.confirmed_at,
        setup_at: e.setup_at, pattern: e.pattern })), c.events.slice(-120));
      if (c.kind === 'positive') assert.equal(c.decision.event, 'BUY_STRUCTURE');
      else if (c.kind === 'suppressed') assert.equal(c.decision.event, '');
      else {
        assert.equal(c.events.filter(e => e.confirmed_at.slice(0, 10) === c.when.slice(0, 10)).length, 0);
        assert.equal(record.confirmations.filter(e => e.confirmed_at.slice(0, 10) === c.when.slice(0, 10)).length, 0);
      }
    });
  });
