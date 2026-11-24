import assert from 'node:assert/strict';
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { chartModel } from '../src/chart_model.mjs';
import { mountWorkbench } from '../src/workbench.mjs';
import { COMMAND, RECEIPT, WORKBENCH, deepLink, TIMEFRAMES } from '../src/workbench_data.mjs';

const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const board = { key: 'cn_concept:886054.TI', name: '光刻机' };
const related = { key: 'cn_concept:886069.TI', name: '人形机器人' };
const time = '2026-11-20 15:00:00';
const bar = { timestamp: time, open: 10, high: 12, low: 9, close: 11, volume: 100, ma55: 10, complete: true };
const frames = Object.fromEntries(TIMEFRAMES.map(tf => [tf, { status: 'INSUFFICIENT_HISTORY', expected_end: time }]));
const row = (symbol, name) => ({ symbol, name, sectors: [board.key], frames });
const empty = { revision: 0, sectors: [], members: {}, catalog: [board, related] };
const summary = { expected: 0, ready_symbols: 0, candidate_symbols: 0, blocked_symbols: 0, target_15m: time };

test('confirmed focus handles the draft and offers related catalogue matches for manual confirmation', async () => {
  const dom = new JSDOM('<div id="mount"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const { document } = dom.window, calls = [], commands = new Map(), replies = [];
  const saved = { ...empty, revision: 1, sectors: [{ ...board, symbols: ['600000.SH'] }], members: { '600000.SH': [board.key] } };
  let focus = empty;
  const api = {
    async extDataRows(id) {
      if (id === COMMAND) return { rows: [...commands.values()] };
      if (id === RECEIPT) return { rows: replies };
      assert.equal(id, WORKBENCH);
      return { rows: [{ payload_json: JSON.stringify({ focus, rows: [], summary, state: { phase: 'SCOPE_PAUSED' } }) }] };
    },
    async extDataIngest(id, { rows }) {
      assert.equal(id, COMMAND);
      const command = rows[0], args = JSON.parse(command.payload_json);
      calls.push(args); commands.set(command.scope, command);
      focus = saved;
      replies.push({ ...command, payload_json: JSON.stringify({ ok: true, focus }) });
    },
  };
  const dispose = mountWorkbench(document.querySelector('#mount'), { api }, { ioOptions: { uuid: randomUUID } });
  try {
    await tick();
    assert.equal(calls.length, 0);
    const chip = [...document.querySelectorAll('.mr-focus-chip button')].find(b => b.textContent.includes('物理AI'));
    chip.click();
    const input = document.querySelector('[data-role="query"]');
    assert.equal(document.activeElement, input);
    assert.match(document.querySelector('[data-role="matches"]').textContent, /人形机器人/);
    input.value = '';
    input.dispatchEvent(new dom.window.Event('input'));
    document.querySelector('[data-role="apply"]').click();
    await tick();
    assert.deepEqual(calls, [{ action: 'save', expected_revision: 0, selection: [board.key] }]);
    assert.match(document.querySelector('[data-role="summary"]').textContent, /已保存 1 个板块/);
  } finally { dispose(); dom.window.close(); }
});

test('row click and six-period switching ignore the obsolete chart receipt', async () => {
  const dom = new JSDOM('<div id="mount"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const { document } = dom.window;
  const a = '600000.SH', b = '000001.SZ', rows = [row(a, '第一只'), row(b, '第二只')];
  const commands = new Map(), replies = [], chartRequests = [], shown = [];
  const api = {
    async extDataRows(id) {
      if (id === COMMAND) return { rows: [...commands.values()] };
      if (id === RECEIPT) return { rows: replies };
      return { rows: [{ payload_json: JSON.stringify({ focus: {
        ...empty, revision: 1, sectors: [{ ...board, symbols: [a, b] }], members: { [a]: [board.key], [b]: [board.key] },
      }, rows, summary: { ...summary, expected: 2 }, state: {} }) }] };
    },
    async extDataIngest(id, body) {
      assert.equal(id, COMMAND);
      const command = body.rows[0], args = JSON.parse(command.payload_json);
      chartRequests.push(args); commands.set(command.scope, command);
      if (args.symbol === b) {
        for (const request of [command, ...(replies.length ? [] : commands.values())]) {
          const select = JSON.parse(request.payload_json);
          replies.push({ ...request, payload_json: JSON.stringify({ ...select, bars: [{ ...bar, close: select.symbol === b ? 11 : 999 }],
            grid: [time], events: [], asof: time, status: 'READY' }) });
        }
      }
    },
  };
  const fakeChart = () => ({ update(model) { shown.push([model.period, model.bars[0].close]); },
    focus() {}, dispose() {}, setLevels() {} });
  const dispose = mountWorkbench(document.querySelector('#mount'), { api }, {
    ioOptions: { uuid: randomUUID, wait: () => tick() }, makeChart: fakeChart,
  });
  try {
    await tick();
    document.querySelector(`.mr-stock-name[data-stock="${a}"]`).click();
    await tick();
    document.querySelector(`[data-stock="${b}"][data-tf="weekly"]`).click();
    await tick();
    assert.match(document.querySelector('[data-role="selection"]').textContent, /第二只/);
    assert.deepEqual(shown, [['weekly', 11]]);
    assert.equal(document.querySelectorAll('[data-period]').length, 6);
    document.querySelector('[data-period="120m"]').click();
    await tick();
    assert.deepEqual(chartRequests.map(request => request.timeframe), ['15m', 'weekly', '120m']);
    assert.deepEqual(shown.at(-1), ['120m', 11]);
  } finally { dispose(); dom.window.close(); }
});

test('weekly stamps and frozen event links use the stored exchange end', () => {
  const url = '?symbol=600000.SH&period=15m&source=QUANTDASH_LEGACY&event=2026-09-24T15%3A00%3A00&event_id=frozen';
  assert.deepEqual(deepLink(url), { symbol: '600000.SH', source: 'archive', timeframe: '15m',
    anchor: '2026-09-24 15:00:00', eventId: 'frozen' });
  const at = '2026-09-24 15:00:00';
  const model = chartModel({ grid: [at], bars: [{ ...bar, timestamp: at }], events: [{ at }] },
    { timeframe: 'weekly', anchor: at });
  assert.equal(model.anchor, 0);
  assert.equal(model.bars[0].timestamp, at);
  assert.equal(model.times[0], Date.parse('2026-09-24T15:00:00+08:00') / 1000);
});
