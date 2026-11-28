import assert from 'node:assert/strict';
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { AVERAGE_COLORS, chartModel } from '../src/chart_model.mjs';
import { activationAccess, activationResults } from '../src/activation_results.mjs';
import { filterRows } from '../src/stock_matrix.mjs';
import extension from '../src/extension.mjs';
import { mountWorkbench } from '../src/workbench.mjs';
import { COMMAND, RECEIPT, RUNTIME, WORKBENCH, createWorkbenchIO, deepLink, TIMEFRAMES } from '../src/workbench_data.mjs';

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

test('transient Ext Data read while a receipt is written keeps the original command', async () => {
  let submitted, writes = 0, polls = 0;
  const api = {
    async extDataRows(id) {
      if (id === COMMAND) return { rows: submitted ? [submitted] : [] };
      if (id !== RECEIPT) throw Error('unexpected table');
      if (++polls === 1) throw Object.assign(Error('500 Internal Server Error'), { status: 500 });
      return { rows: [{ ...submitted, payload_json: JSON.stringify({ ok: true, rows: [] }) }] };
    },
    async extDataIngest(id, { rows }) {
      assert.equal(id, COMMAND);
      writes++;
      [submitted] = rows;
    },
  };
  const io = createWorkbenchIO(api, { uuid: randomUUID, wait: async () => {} });
  try {
    assert.deepEqual(await io.submit('members', { board: board.key }), { ok: true, rows: [] });
    assert.equal(writes, 1);
    assert.equal(polls, 2);
  } finally { io.dispose(); }
});

test('two clients serialize command ingests without locking receipt polling', async () => {
  const commands = new Map(), replies = new Map();
  let queue = Promise.resolve(), active = 0, peak = 0;
  const locks = {
    request(name, fn) {
      assert.equal(name, COMMAND);
      const result = queue.then(fn);
      queue = result.catch(() => {});
      return result;
    },
  };
  const api = {
    async extDataRows(id) {
      return { rows: [...(id === COMMAND ? commands : replies).values()] };
    },
    async extDataIngest(id, { rows: [row] }) {
      assert.equal(id, COMMAND);
      peak = Math.max(peak, ++active);
      await tick();
      active--;
      commands.set(row.scope, row);
      replies.set(row.scope, { ...row, payload_json: row.payload_json });
    },
  };
  const windows = [createWorkbenchIO(api, { uuid: randomUUID, locks }),
    createWorkbenchIO(api, { uuid: randomUUID, locks })];
  try {
    const results = await Promise.all(windows.map((io, i) => io.submit('edit', { draft: i })));
    assert.deepEqual(results, [{ draft: 0 }, { draft: 1 }]);
    assert.equal(peak, 1);
    assert.equal(commands.size, 2);
  } finally { windows.forEach(io => io.dispose()); }
});

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

test('period filters, current counters and today results use the same published window', () => {
  const day = time.slice(0, 10);
  const early = `${day} 09:45:00`, near = `${day} 14:45:00`;
  const targets = Object.fromEntries(TIMEFRAMES.map(tf => [tf, time]));
  const item = (symbol, intraday, state, weekly = []) => ({
    ...row(symbol, symbol), frames: {
      '15m': { activation: { state, events: intraday } },
      weekly: { activation: { state: weekly.length ? 'ACTIVATED' : 'INACTIVE', events: weekly } },
    },
  });
  const events = (...times) => times.map(at => ({ at }));
  const rows = [
    item('600000.SH', events(early), 'ACTIVATED', events(time)),
    item('600001.SH', events(near), 'ACTIVATED'),
    item('600002.SH', events(early, time), 'ACTIVATED'),
    item('600003.SH', events(early), 'PENDING'),
    item('600004.SH', [], 'MISSING'),
    item('600005.SH', [], 'INACTIVE'),
  ];
  const view = { result_id: 'r1', focus: { ...empty, revision: 1 }, rows,
    summary: { expected: rows.length, market_day: day }, activation_targets: targets };
  const status = { result_id: 'r1', focus_revision: 1, taxonomy_run: 'verified', scope_count: rows.length,
    market_day: day, activation_targets: targets, worker_alive: true, status_at_ms: Date.now() };
  const access = activationAccess(view, status);
  const results = activationResults(rows, access);
  const minute = results['15m'];
  assert.deepEqual([minute.ready, minute.pending, minute.count, minute.today.length], [4, 1, 1, 4]);
  assert.equal(minute.today[0].events.length, 2);
  const sorted = filterRows(rows, { period: '15m', state: 'ACTIVATED', order: 'candidate' }, access);
  assert.deepEqual(sorted.map(item => item.symbol), ['600002.SH', '600001.SH', '600000.SH']);
  for (const [state, symbol] of [['PENDING', '600003.SH'], ['MISSING', '600004.SH'], ['INACTIVE', '600005.SH']]) {
    assert.equal(filterRows(rows, { period: '15m', state, order: 'symbol' }, access)[0].symbol, symbol);
  }
  const rolled = activationAccess(view, { ...status, activation_targets: { ...targets, '15m': null } });
  assert.equal(activationResults(rows, rolled)['15m'].count, null);
  assert.equal(activationResults(rows, rolled).weekly.count, 1);
  for (const broken of [{ result_id: 'r2' }, { focus_revision: 2 }, { publisher_error: '失败' }, { taxonomy_run: 'other' }]) {
    assert.equal(activationResults(rows, activationAccess(view, { ...status, ...broken }))['15m'].count, null);
  }
});

test('confirmed rows use exact chart anchors and moving average toggles consume no commands', async () => {
  const dom = new JSDOM('<div id="mount"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const { document } = dom.window;
  const symbol = '600000.SH', day = time.slice(0, 10), early = `${day} 14:30:00`;
  const targets = Object.fromEntries(TIMEFRAMES.map(tf => [tf, time]));
  const events = [{ id: 'past', at: early, mode: 'REPLAY' }, { id: 'now', at: time, mode: 'LIVE', price: 11 }];
  const another = { ...related, symbols: ['000001.SZ'] };
  const entries = [{ ...row(symbol, '测试股'), frames: {
    ...frames, '30m': { status: 'READY', expected_end: time, activation: { state: 'ACTIVATED', events } },
  } }, { ...row('000001.SZ', '其他'), sectors: [another.key] }];
  const view = { result_id: 'r1', summary: { ...summary, expected: 2, market_day: day },
    focus: { ...empty, revision: 1, sectors: [{ ...board, symbols: [symbol] }, another],
      members: { [symbol]: [board.key], '000001.SZ': [another.key] } },
    activation_targets: targets, rows: entries };
  const runtime = { result_id: 'r1', focus_revision: 1, taxonomy_run: 'verified', market_day: day,
    scope_count: 2, activation_targets: targets, worker_alive: true, status_at_ms: Date.now() };
  const commands = new Map(), replies = [], requested = [], visible = [];
  const bars = [early, time].map((timestamp, index) => ({ ...bar, timestamp, close: 10 + index,
    ...Object.fromEntries(Object.keys(AVERAGE_COLORS).map(n => [`ma${n}`, index ? 10 : null])) }));
  const api = {
    async extDataRows(id) {
      if (id === COMMAND) return { rows: [...commands.values()] };
      if (id === RECEIPT) return { rows: replies };
      return { rows: [{ payload_json: JSON.stringify(id === RUNTIME ? runtime : view) }] };
    },
    async extDataIngest(id, { rows: [command] }) {
      assert.equal(id, COMMAND);
      commands.set(command.scope, command);
      const request = JSON.parse(command.payload_json);
      requested.push(request);
      replies.push({ ...command, payload_json: JSON.stringify({ ...request, bars, events, asof: time, status: 'READY' }) });
    },
  };
  const fakeChart = (el, readout) => ({ update(model) { readout(model.bars.at(-1)); },
    setAverageVisible(period, value) { visible.push([period, value]); }, focus() {}, setLevels() {}, dispose() {} });
  const dispose = mountWorkbench(document.querySelector('#mount'), { api }, {
    ioOptions: { uuid: randomUUID, wait: () => tick() }, makeChart: fakeChart,
  });
  const find = selector => document.querySelector(selector);
  try {
    await tick();
    find(`button[data-board="${board.key}"]`).click();
    find('[data-result-period="30m"]').click();
    assert.equal(find('[data-result-period="30m"] b').textContent, '1');
    assert.match(find('[data-role="result-controls"]').textContent, /已算 1\/1/);
    find('[data-result-window="today"]').click();
    assert.equal(find('[data-result-window="today"]').getAttribute('aria-pressed'), 'true');
    assert.equal(find('[data-result-period="30m"] b').textContent, '1');
    find('[data-act="expand-results"]').click();
    find(`[data-result-detail="${symbol}"]`).click();
    assert.match(find('[data-signal="past"]').textContent, /补算/);
    find('[data-signal="past"]').click();
    await tick();
    assert.deepEqual(requested[0], { symbol, timeframe: '30m', source: 'focus', anchor: early, eventId: 'past' });
    assert.equal(document.querySelectorAll('[data-ma]').length, 6);
    assert.equal(find('[data-ma="233"] span').textContent, '10.00');
    find('[data-ma="233"]').click();
    assert.deepEqual(visible, [['233', false]]);
    assert.equal(requested.length, 1);
    find(`[data-stock="${symbol}"][data-tf="30m"]`).click();
    await tick();
    assert.deepEqual(requested.at(-1), { symbol, timeframe: '30m', source: 'focus', anchor: time, eventId: 'now' });
    assert.equal(find('[data-ma="233"]').getAttribute('aria-pressed'), 'false');
  } finally { dispose(); dom.window.close(); }
});

test('all six SMAs retain the same bar timestamps and break at an unavailable slot', () => {
  const dates = [0, 1, 2].map(i => `2026-11-2${i} 15:00:00`);
  const values = Object.fromEntries(Object.keys(AVERAGE_COLORS).map((n, i) => [`ma${n}`, i + 1]));
  const model = chartModel({ grid: dates, bars: [dates[0], dates[2]].map(timestamp => ({
    ...bar, ...values, timestamp,
  })) }, { timeframe: 'daily' });
  for (const [i, points] of Object.values(model.averages).entries()) {
    assert.equal(points[0].value, i + 1);
    assert.equal(points[0].color, 'transparent');
    assert.equal(points[1].value, undefined);
    assert.equal(points[2].time, model.bars.at(-1).time);
  }
});

test('saved directions stay flat while the full matrix can expand industry levels', async () => {
  const dom = new JSDOM('<div id="mount"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const { document } = dom.window;
  const day = time.slice(0, 10), [first, missing, last, unknown] = ['300001.SZ', '300002.SZ', '300003.SZ', '300004.SZ'];
  const industry = ['BK1201', 'BK1036', 'BK1325'].map((code, i) => ({ kind: 'industry', level: i + 1,
    key: `dc:industry:${code}.DC`, name: ['电子', '半导体', '半导体材料'][i] }));
  industry.forEach((node, i) => { node.parent = industry[i - 1]?.key; });
  const [root, branch, leaf] = industry, concept = { ...board, symbols: [missing, unknown] };
  const members = { [first]: [leaf.key, branch.key], [missing]: [leaf.key, branch.key, board.key],
    [last]: [leaf.key, branch.key], [unknown]: [board.key] };
  const targets = Object.fromEntries(TIMEFRAMES.map(tf => [tf, time]));
  const entries = Object.keys(members).map((symbol, i) => ({ ...row(symbol, `证券${i + 1}`), sectors: members[symbol],
    frames: { ...frames, '15m': { expected_end: time, status: i % 2 === 0 ? 'READY' : 'GAP_IN_REPLAY_WINDOW',
      activation: { state: i % 2 === 0 ? 'ACTIVATED' : 'MISSING', events: i % 2 === 0 ? [{ id: symbol, at: time }] : [] } },
      '30m': { expected_end: time, status: 'WAITING_INPUT', activation: { state: 'PENDING', events: [] } } } }));
  const view = { result_id: 'r1', summary: { ...summary, expected: 4, market_day: day }, activation_targets: targets,
    focus: { ...empty, revision: 1, members, catalog: [...industry, concept],
      sectors: [{ ...leaf, symbols: [first, missing, last] }, { ...branch, symbols: [first, missing, last] }, concept],
      groups: [{ id: 'overlap', name: '交叉组', boards: [leaf.key, board.key] }] },
    industry_paths: { [first]: leaf.key, [missing]: leaf.key, [last]: leaf.key }, rows: entries };
  const runtime = { result_id: 'r1', focus_revision: 1, taxonomy_run: 'verified', market_day: day, scope_count: 4,
    activation_targets: targets, worker_alive: true, status_at_ms: Date.now(),
    coverage: { expected: 4, blocking_by_timeframe: { '15m': { GAP_IN_REPLAY_WINDOW: 2 } } } };
  const commands = new Map(), replies = new Map(), requests = [], previews = [];
  const api = {
    async extDataRows(id) {
      if (id === COMMAND || id === RECEIPT) return { rows: [...(id === COMMAND ? commands : replies).values()] };
      return { rows: [{ payload_json: JSON.stringify(id === RUNTIME ? runtime : view) }] };
    },
    async extDataIngest(id, { rows: [command] }) {
      const request = JSON.parse(command.payload_json);
      requests.push(request);
      commands.set(command.scope, command);
      replies.set(command.scope, { ...command, payload_json: JSON.stringify({ ...request, bars: [bar], asof: time,
        diagnostic: request.symbol === missing ? { target: time, reason: 'GAP_IN_REPLAY_WINDOW',
          missing_raw: [time, time], queue: 'COOLING', retry_at: `${day} 16:00:00` } : null }) });
    },
  };
  const dispose = mountWorkbench(document.querySelector('#mount'), { api, ui: { openStockPreview: p => previews.push(p) } }, {
    ioOptions: { uuid: randomUUID, wait: () => tick() }, makeChart: () => ({ update() {}, setLevels() {}, dispose() {} }),
  });
  const find = selector => document.querySelector(selector);
  const symbols = () => [...document.querySelectorAll('.mr-stock-name[data-stock]')].map(x => x.dataset.stock);
  try {
    await tick();
    assert.equal(find(`[data-board="${root.key}"]`), null);
    assert.equal(find('[data-activation-group]'), null);
    assert.match(find('[data-matrix-group="unclassified"]').textContent, /未分类/);
    assert.equal(document.querySelectorAll('.mr-matrix-group[data-level="1"]').length, 2);
    assert.equal(find(`[data-matrix-group="${leaf.key}"]`), null);
    find(`[data-industry-detail="${root.key}"]`).click();
    assert.ok(find(`[data-matrix-group="${branch.key}"]`));
    assert.equal(find(`[data-matrix-group="${leaf.key}"]`), null);
    find(`.mr-matrix-group [data-industry-detail="${branch.key}"]`).click();
    assert.ok(find(`[data-matrix-group="${leaf.key}"]`));
    find(`[data-matrix-group="${root.key}"]`).click();
    assert.deepEqual(symbols(), [unknown]);
    find(`[data-matrix-group="${root.key}"]`).click();
    find(`[data-industry-detail="${root.key}"]`).click();
    find(`[data-board="${leaf.key}"]`).click();
    assert.equal(find('[data-matrix-group]'), null);
    assert.deepEqual(new Set(symbols()), new Set([first, missing, last]));
    find(`[data-board="${branch.key}"]`).click();
    if (find('[data-act="subgroups"]').getAttribute('aria-pressed') === 'true') find('[data-act="subgroups"]').click();
    assert.equal(find('[data-matrix-group]'), null);
    find('[data-act="subgroups"]').click();
    assert.ok(find(`[data-matrix-group="${leaf.key}"]`));
    find('[data-board="group:overlap"]').click();
    assert.equal(new Set(symbols()).size, 4);
    assert.equal(symbols().length, 4);
    assert.equal(find('[data-matrix-group]'), null);
    assert.equal(requests.length, 0);
    find(`[data-signal="${first}"]`).click();
    await tick();
    find('[data-act="preview"]').click();
    assert.deepEqual(previews[0].navList.map(x => x.symbol), [first, last]);
    find('[data-act="next"]').click();
    await tick();
    assert.equal(requests.at(-1).symbol, last);
    find(`[data-board="${board.key}"]`).click();
    assert.equal(find('[data-matrix-group]'), null);
    assert.match(find('[data-role="nav-context"]').textContent, /不在当前范围/);
    assert.equal(find('[data-act="previous"]').disabled, true);
    find('[data-board=""]').click();
    const order = find('[data-role="order"]');
    order.value = 'symbol'; order.dispatchEvent(new dom.window.Event('change'));
    find(`.mr-stock-name[data-stock="${first}"]`).click();
    await tick();
    find('[data-act="next"]').click();
    await tick();
    assert.equal(requests.at(-1).symbol, missing);
    assert.match(find('[data-role="stock-diagnostic"]').textContent, /冷却至 2026-11-20 16:00:00/);
    find('[data-block-period="15m"]').click();
    assert.deepEqual(symbols(), [missing, unknown]);
    find('[data-result-period="30m"]').click();
    const lens = find('[data-role="filter-period"]');
    assert.equal(lens.value, '30m');
    find(`[data-stock="${first}"][data-tf="30m"][data-state="PENDING"]`).click();
    await tick();
    assert.match(find('[data-role="stock-diagnostic"]').textContent, /2026-11-20 15:00:00.*等待有效数据/s);
    lens.value = '15m'; lens.dispatchEvent(new dom.window.Event('change'));
    assert.equal(find('[data-result-period="15m"]').getAttribute('aria-pressed'), 'true');
  } finally { dispose(); dom.window.close(); }
});
