import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import extension from '../src/extension.mjs';
import { mountObserver, completedBoundary } from '../src/observer.mjs';
import { createView, tableRows, chartBars } from '../src/presentation.mjs';
import { consumeBars } from '../src/state.mjs';
import { scenario, toMinuteSessions } from './cases.mjs';
import { aggregateSessions } from '../src/aggregate.mjs';
import { loadInstrument, loadCatalog, refreshInstruments } from '../src/host_data.mjs';

const pause = () => new Promise(resolve => setTimeout(resolve, 20));
test('Page Pack manifest is self-contained, static and removable', async () => {
  assert.equal(extension.id, 'lan.ma55'); assert.equal(extension.apiVersion, 1);
  assert.equal(extension.version, '0.3.2');
  assert.equal(extension.routes[0].path, '/lan-ma55');
  assert.equal(extension.navigation[0].label, 'MA55 结构观察');
  const built = await import('../dist/extension.js');
  assert.equal(built.default.id, extension.id);
  assert.equal(built.default.version, extension.version);
  assert.equal(typeof built.default.routes[0].mount, 'function');
});
test('today never relabels old signals; replay remains distinct from live', () => {
  const c = scenario([[100, 105.5, 99.6, 105, 600_000]]);
  const record = consumeBars(null, c.bars, { ...c, symbol: '600000.SH', seenAt: '2026-11-07 18:00:00' }).record;
  const item = { symbol: '600000.SH', name: '合成证券', record, asset_type: 'stock',
    chart: chartBars(c.bars, c.factors, c.sessions) };
  const model = { now: '2026-11-07 18:00:00', items: [item], asset: 'all', tab: 'today' };
  assert.equal(tableRows(model).length, 0);
  assert.equal(tableRows({ ...model, tab: 'history' }).length, 1);
  assert.equal(tableRows({ ...model, tab: 'watch' }).length, 0);
  const dom = new JSDOM('<main></main>'), actions = [];
  const view = createView(dom.window.document.querySelector('main'), {
    action: (...args) => actions.push(args), change: () => {},
  });
  view.render({ ...model, tab: 'history', groups: [], results: [], page: 0, total: 1,
    query: '', selectedSymbol: item.symbol, selectedEvent: record.confirmations[0].event_id,
    uiAvailable: false });
  assert.match(dom.window.document.body.textContent, /历史重放/);
  assert.match(dom.window.document.body.textContent, /首次看到/);
  assert.match(dom.window.document.body.textContent, /离均线较远，不追价/);
  assert.match(dom.window.document.querySelector('svg').textContent, /BUY脚推/);
  assert.match(dom.window.document.body.textContent, /当前宿主未提供个股详情入口/);
  assert.ok(dom.window.document.querySelector('svg[aria-label]'));
  assert.ok(dom.window.document.querySelector('[data-action="preview"]').disabled);
  view.destroy(); assert.equal(dom.window.document.querySelector('.lm'), null);
});
test('mount loading, empty, group switch, public stock preview and disposal', async () => {
  const dom = new JSDOM('<main></main>', { url: 'http://localhost/lan-ma55', pretendToBeVisual: true });
  let ranges = 0, preview;
  const api = {
    watchlistList: async () => ({ symbols: [{ symbol: '600000.SH', name: '合成证券', group_ids: ['g'] }] }),
    watchlistGroups: async () => ({ groups: [{ id: 'g', name: '合成分组' }, { id: 'empty', name: '空分组' }] }),
    instrumentNames: async () => ({ names: {} }), indexDaily: async () => ({ rows: [{ date: '2026-11-06' }] }),
    klineMinuteRange: async symbol => { ranges++; return { symbol, asset_type: 'stock', sessions: [], source: 'none' }; },
  };
  const cleanup = mountObserver(dom.window.document.querySelector('main'), { api, ui: {
    openStockPreview: value => { preview = value; return true; },
  } });
  assert.match(dom.window.document.body.textContent, /MA55 结构观察/);
  assert.match(dom.window.document.body.textContent, /MA55全市场后台未接通/);
  assert.match(dom.window.document.body.textContent, /BLOCKED_BY_OFFICIAL_EXTENSION_CONTRACT/);
  const query = dom.window.document.querySelector('[data-control="query"]');
  query.value = '600000'; query.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await pause();
  assert.equal(dom.window.document.querySelector('[data-control="query"]').value, '600000');
  assert.match(dom.window.document.body.textContent, /今日尚无已确认/);
  dom.window.document.querySelector('[data-action="tab"][data-tab="data"]').click();
  assert.match(dom.window.document.body.textContent, /数据不足/);
  dom.window.document.querySelector('[data-action="preview"]').click();
  assert.equal(preview.symbol, '600000.SH'); assert.deepEqual(preview.navList, [{ symbol: '600000.SH', name: '合成证券' }]);
  const group = dom.window.document.querySelector('[data-control="group"]');
  group.value = 'empty'; group.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  await pause(); assert.match(dom.window.document.body.textContent, /当前分组为空/);
  const before = ranges; cleanup(); await pause();
  assert.equal(ranges, before); assert.equal(dom.window.document.querySelector('.lm'), null);
  dom.window.close();
});
test('failed host request is shown and cannot leave an active mounted page after cleanup', async () => {
  const dom = new JSDOM('<main></main>', { url: 'http://localhost', pretendToBeVisual: true });
  const cleanup = mountObserver(dom.window.document.querySelector('main'), { api: {
    watchlistList: async () => { throw new Error('合成权限失败'); },
    watchlistGroups: async () => ({ groups: [] }), indexDaily: async () => ({ rows: [] }),
  } });
  await pause(); assert.match(dom.window.document.body.textContent, /合成权限失败/);
  cleanup(); assert.equal(dom.window.document.querySelector('.lm'), null); dom.window.close();
});
test('completion boundary updates at most once per finished 15m and skips lunch', () => {
  assert.equal(completedBoundary('2026-11-06 09:45:59'), '');
  assert.equal(completedBoundary('2026-11-06 09:46:00'), '2026-11-06 09:45:00');
  assert.equal(completedBoundary('2026-11-06 13:00:00'), '2026-11-06 11:30:00');
  assert.equal(completedBoundary('2026-11-06 13:33:00'), '2026-11-06 13:30:00');
});
test('host adapter uses 10/20 local days only, no private source client, and only verified asset ticks', async () => {
  const c = scenario([], 160), sessions = toMinuteSessions(c.bars), calls = [];
  const api = { klineMinuteRange: async (symbol, days) => {
    calls.push(days); return { symbol, asset_type: 'etf', sessions: sessions.map(s => ({ ...s,
      rows: s.rows.map(r => ({ ...r, open: null })) })), source: 'local' };
  }, klineDaily: async symbol => ({ symbol, asset_type: 'etf', source: 'enriched', rows: [] }) };
  const loaded = await loadInstrument(api, '588170.SH', '2026-11-07 18:00:00');
  assert.deepEqual(calls, [10, 20]); assert.equal(loaded.tick, .001);
  assert.equal(loaded.aggregated.fifteen.filter(b => b.valid).length, 0);
});
test('one daily request failure cannot discard another symbol in a minute batch', async () => {
  const rows = toMinuteSessions(scenario([], 16).bars)[0].rows;
  const day = rows[0].datetime.slice(0, 10), items = ['600000.SH', '600001.SH'].map(symbol => ({ symbol,
    asset_type: 'stock', today: [], tick: .01 }));
  let batchArgs;
  const api = {
    klineMinuteBatch: async (...args) => { batchArgs = args; return { data: Object.fromEntries(items.map(i => [i.symbol, rows])), incremental: false }; },
    klineDaily: async symbol => {
      if (symbol === '600000.SH') throw new Error('合成单股失败');
      return { symbol, asset_type: 'stock', source: 'enriched', rows: [{ date: day, close: 100, raw_close: 100 }] };
    },
  };
  const result = await refreshInstruments(api, items, `${day} 16:00:00`);
  assert.equal(batchArgs[2], true); assert.match(result[0].blocked, /单股失败/);
  assert.equal(result[1].blocked, ''); assert.ok(result[1].aggregated.fifteen.every(b => b.valid));
});
test('market calendar is read once through the public index API, names are batched', async () => {
  let names, calendar;
  const catalog = await loadCatalog({ watchlistList: async () => ({ symbols: [{ symbol: '588170.SH' }] }),
    watchlistGroups: async () => ({ groups: [] }), instrumentNames: async s => { names = s; return { names: { '588170.SH': '合成ETF' } }; },
    indexDaily: async (...args) => { calendar = args; return { rows: [{ date: '2026-11-06' }] }; } });
  assert.deepEqual(names, ['588170.SH']); assert.deepEqual(calendar, ['000001.SH', 120]);
  assert.equal(catalog.entries[0].name, '合成ETF');
});
test('chart breaks MA55 at actual missing-session and factor boundaries', () => {
  const c = scenario([], 128), rows = [...c.bars]; rows.splice(80, 16);
  const output = chartBars(rows, c.factors, c.sessions);
  assert.ok(Number.isNaN(output[80].ma55));
  assert.ok(Number.isNaN(output.at(-1).ma55));
  assert.equal(aggregateSessions(toMinuteSessions(c.bars), '2026-11-07 18:00:00').five.length, 384);
});
