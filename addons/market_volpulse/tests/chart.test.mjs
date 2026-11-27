import { test } from 'node:test';
import assert from 'node:assert/strict';
import { charts } from '../src/chart.mjs';
import extension from '../src/extension.mjs';

test('manifest namespace and route', () => {
  assert.equal(extension.id, 'market.volpulse');
  assert.equal(extension.routes[0].path, '/market-volpulse');
  assert.equal(typeof extension.routes[0].mount, 'function');
});

test('empty series does not fabricate lines', () => {
  assert.match(charts([])[0], /等待有效/);
});

test('gap stays disconnected and both charts exist', () => {
  const points = [
    { k: 0, today: 100, previous: 100, today_delta: 20, previous_delta: 20, multiple: null },
    { k: 1, today: null, previous: 200, today_delta: null, previous_delta: 100, multiple: null },
    { k: 2, today: 300, previous: 250, today_delta: 100, previous_delta: 50, multiple: 2 },
  ];
  const [up, low] = charts(points);
  assert.match(up, /data-chart="cum"/);
  assert.match(low, /data-chart="delta"/);
  assert.doesNotMatch(up + low, /NaN|Infinity/);
  assert.match(up, /class="vp-day-line" d="M[\d.]+,[\d.]+ M[\d.]+,[\d.]+"/);
});

test('auction and transient snapshot draw separately from completed points', () => {
  const points = [{ k: 0, today: 120, previous: 100 }];
  const [up, low] = charts(points, false, {
    auction: { today: 60, previous: 45 },
    snapshot: { k: 1, elapsed: 30, today: 145, multiple: 1.3 },
    events: [{ k: 0, direction: 'SURGE' }],
  });
  assert.match(up, /circle cx="66"/);
  assert.match(up, /vp-live-tail/);
  assert.match(up, /vp-snapshot-dot/);
  assert.match(up, /stroke-width="1.5"/);
  assert.match(low, /vp-snapshot-dot/);
  assert.deepEqual(points, [{ k: 0, today: 120, previous: 100 }]);
});

test('malformed index cannot inject SVG path attributes', () => {
  const [up, low] = charts([{ k: '" onload="alert(1)', today: 10 }, null]);
  assert.doesNotMatch(up + low, /onload|NaN|Infinity/);
});
