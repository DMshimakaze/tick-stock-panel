import assert from 'node:assert/strict';
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { chartModel } from '../src/chart_model.mjs';
import extension from '../src/extension.mjs';
import { mountWorkbench } from '../src/workbench.mjs';
import { COMMAND, RECEIPT, RUNTIME, WORKBENCH, deepLink, TIMEFRAMES } from '../src/workbench_data.mjs';

const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const board = { key: 'cn_concept:886054.TI', name: '光刻机' };
const related = { key: 'cn_concept:886069.TI', name: '人形机器人', aliases: ['物理AI'] };
const time = '2026-11-20 15:00:00';
const bar = { timestamp: time, open: 10, high: 12, low: 9, close: 11, volume: 100, ma55: 10, complete: true };
const frames = Object.fromEntries(TIMEFRAMES.map(tf => [tf, { status: 'INSUFFICIENT_HISTORY', expected_end: time }]));
const row = (symbol, name) => ({ symbol, name, sectors: [board.key], frames });
const empty = { revision: 0, sectors: [], members: {}, catalog: [board, related],
  aliases: { [related.key]: related.aliases }, groups: [], taxonomy: { run: 'verified' } };
const summary = { expected: 0, ready_symbols: 0, candidate_symbols: 0, blocked_symbols: 0, target_15m: time };

test('confirmed focus handles the draft and offers explicit local alias matches for manual confirmation', async () => {
  const dom = new JSDOM('<div id="mount"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const { document } = dom.window, calls = [], commands = new Map(), replies = [];
  const saved = { ...empty, revision: 1, sectors: [{ ...board, symbols: ['600000.SH'] }], members: { '600000.SH': [board.key] } };
  let focus = empty;
  const api = {
    async extDataRows(id) {
      if (id === COMMAND) return { rows: [...commands.values()] };
      if (id === RECEIPT) return { rows: replies };
      if (id === RUNTIME) return { rows: [] };
      assert.equal(id, WORKBENCH);
      return { rows: [{ payload_json: JSON.stringify({ focus, rows: [], summary, state: { phase: 'SCOPE_PAUSED' } }) }] };
    },
    async extDataIngest(id, { rows }) {
      assert.equal(id, COMMAND);
      const command = rows[0], args = JSON.parse(command.payload_json);
      calls.push(args); commands.set(command.scope, command);
      const payload = args.action === 'preview' ? { ok: true, added: 1, removed: 0, retained: 0,
        total_memberships: 1, unique_symbols: 1, preview_id: command.request_id }
        : { ok: true, focus: (focus = saved) };
      replies.push({ ...command, payload_json: JSON.stringify(payload) });
    },
  };
  const dispose = mountWorkbench(document.querySelector('#mount'), { api }, { ioOptions: { uuid: randomUUID } });
  try {
    await tick();
    assert.equal(calls.length, 0);
    const manager = document.querySelector('[data-role="manager"]');
    assert.equal(manager.open, false);
    manager.open = true;
    const chip = [...document.querySelectorAll('[data-role="directions"] button')].find(b => b.textContent === '物理AI');
    chip.click();
    const input = document.querySelector('[data-role="query"]');
    assert.equal(document.activeElement, input);
    assert.match(document.querySelector('[data-role="matches"]').textContent, /人形机器人/);
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', isComposing: true }));
    assert.equal(calls.length, 0);
    input.value = '';
    input.dispatchEvent(new dom.window.Event('input'));
    document.querySelector('button[aria-label="添加 光刻机"]').click();
    assert.equal(document.querySelector('[data-role="apply"]').disabled, true);
    document.querySelector('[data-role="preview"]').click();
    await tick();
    assert.deepEqual([...document.querySelectorAll('[data-role="summary"] .mr-note')].map(node => node.textContent),
      ['新增 1', '退出 0', '保留 0']);
    document.querySelector('[data-role="apply"]').click();
    await tick();
    assert.deepEqual(calls.map(({ action, expected_revision, run, selection }) =>
      [action, expected_revision, run, selection]), [
      ['preview', 0, 'verified', [board.key]], ['save', 0, 'verified', [board.key]],
    ]);
    assert.equal(calls[1].preview_id, replies[0].request_id);
    assert.match(document.querySelector('[data-role="summary"]').textContent, /已保存 1 个板块/);
  } finally { dispose(); dom.window.close(); }
});

test('publication failure preserves the last data and missing publication begins pending', async () => {
  const dom = new JSDOM('<div id="mount"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const { document } = dom.window;
  let response;
  const api = { async extDataRows() {
    if (response instanceof Error) throw response;
    return { rows: response ? [{ payload_json: JSON.stringify(response) }] : [] };
  } };
  const dispose = mountWorkbench(document.querySelector('#mount'), { api }, { ioOptions: { uuid: randomUUID } });
  const clickRefresh = () => document.querySelector('[data-act="refresh"]').click();
  try {
    await tick();
    assert.match(document.querySelector('[data-role="stocks"]').textContent, /等待后台发布/);
    assert.equal(document.querySelector('[data-role="coverage"]').textContent, '—');
    assert.equal(document.querySelector('[data-role="progress"]').hidden, true);
    response = { focus: empty, rows: [row('510300.SH', 'ETF')], summary: { ...summary, expected: 1 },
      state: { phase: 'WAITING_NEXT_15M' } };
    clickRefresh();
    await tick();
    assert.match(document.querySelector('[data-role="stocks"]').textContent, /ETF/);
    response = new Error('数据暂不可用');
    clickRefresh();
    await tick();
    assert.equal(document.querySelector('[data-role="task-label"]').textContent, '读取失败');
    assert.match(document.querySelector('[data-role="notice"]').textContent, /数据暂不可用/);
    assert.match(document.querySelector('[data-role="stocks"]').textContent, /ETF/);
  } finally { dispose(); dom.window.close(); }
});

test('a stage change polls status only and a result change preserves the selected cell and table scroll', async () => {
  const dom = new JSDOM('<div id="mount"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const { document } = dom.window, symbol = '600000.SH';
  const focus = { ...empty, revision: 1, sectors: [{ ...board, symbols: [symbol] }], members: { [symbol]: [board.key] } };
  let poll, reads = 0, result = { result_id: 'a', summary, focus, rows: [row(symbol, '甲')] };
  let state = { result_id: 'a', phase: 'B', worker_alive: true, status_at_ms: Date.now(), focus_revision: 1,
    taxonomy_run: 'verified', scope_count: 1, active_operation: 'COLLECTING_NATIVE15', active_symbols: 1 };
  dom.window.setInterval = callback => { poll = callback; };
  const api = { async extDataRows(id) {
    if (id === WORKBENCH) reads++;
    return { rows: [{ payload_json: JSON.stringify(id === RUNTIME ? state : result) }] };
  } };
  const dispose = mountWorkbench(document.querySelector('#mount'), { api }, { ioOptions: { uuid: randomUUID } });
  try {
    await tick();
    state = { ...state, phase: 'A' };
    poll(); await tick();
    assert.equal(reads, 1);
    assert.equal(document.querySelector('[data-role="task-label"]').textContent, '更新行情');
    const select = () => document.querySelector('[data-stock][data-tf="weekly"]');
    const wrap = document.querySelector('[data-role="table-wrap"]');
    select().focus();
    wrap.scrollLeft = 76; wrap.scrollTop = 37;
    result = { ...result, result_id: 'b', rows: [row(symbol, '甲股份')] };
    state = { ...state, result_id: 'b' };
    poll(); await tick();
    assert.equal(reads, 2);
    assert.equal(document.activeElement, select());
    assert.equal(wrap.scrollLeft, 76);
    assert.equal(wrap.scrollTop, 37);
    assert.match(select().getAttribute('aria-label'), /甲股份/);
  } finally { dispose(); dom.window.close(); }
});

test('one installable route serves current observation and frozen history', async () => {
  const manifest = await readFile(new URL('../extension.yaml', import.meta.url), 'utf8');
  assert.match(manifest, /^id: lan\.ma55$/m);
  assert.match(manifest, new RegExp('^version: ' + extension.version.replaceAll('.', '\\.') + '$', 'm'));
  assert.deepEqual(extension.routes.map(route => route.path), ['/lan-ma55']);
  assert.equal(extension.navigation.length, 1);
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
