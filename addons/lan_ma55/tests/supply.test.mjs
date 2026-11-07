import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { loadSupplyStatus } from '../src/supply_status.mjs';
import { mountObserver } from '../src/extension.mjs';

const supplyApi = () => ({
  minuteRefreshStatus: async () => ({ enabled: true, running: true, healthy: false,
    in_trading_hours: false, gate_reason: 'outside_trading_hours', provider: 'quantdash_user',
    repair_only: true, interval_seconds: 120, rounds: 0, last_symbols: 0, last_rows: 0,
    last_requests: 0, last_round_at: null, last_error: null }),
  marketSnapshot: async () => ({ as_of: '2026-11-06', rows: [
    { symbol: '600000.SH', close: 10 }, { symbol: '600000.SH', close: 10 },
    { symbol: '600001.SH', close: null },
  ] }),
  dataStatus: async () => ({ instruments: { symbols_covered: 3, latest_as_of: '2026-11-06' }, stats_pending: true }),
  extensionPackages: async () => [],
});
test('supply uses four official read methods, retaining a candidate count distinct from stock statistics', async () => {
  const result = await loadSupplyStatus(supplyApi());
  assert.deepEqual(result.snapshot.data, { as_of: '2026-11-06', symbols: 1 });
  assert.equal(result.stocks.data.symbols, 3);
  assert.equal(result.stocks.data.pending, true);
  assert.equal(result.backend.data, null);
  assert.equal(result.minute.data.rounds, 0);
});
test('a failed read preserves its dated result and reports the error; first failure stays unknown', async () => {
  const api = supplyApi(), previous = await loadSupplyStatus(api);
  api.marketSnapshot = async () => { throw new Error('快照权限失败'); };
  api.extensionPackages = async () => { throw new Error('管理读取失败'); };
  const result = await loadSupplyStatus(api, previous);
  assert.equal(result.snapshot.data, previous.snapshot.data);
  assert.equal(result.snapshot.checkedAt, previous.snapshot.checkedAt);
  assert.match(result.snapshot.error, /快照权限失败/);
  const first = await loadSupplyStatus(api);
  assert.equal(first.backend.data, undefined);
  assert.match(first.backend.error, /管理读取失败/);
});
test('real supply status remains visible when the watchlist/calendar fails, with no market scan or vendor request', async () => {
  const dom = new JSDOM('<main></main>', { url: 'http://localhost', pretendToBeVisual: true });
  let snapshotCalls = 0;
  const api = { ...supplyApi(),
    marketSnapshot: async () => { snapshotCalls++; return { as_of: '2026-11-06', rows: [{ symbol: '600000.SH', close: 10 }] }; },
    watchlistList: async () => { throw new Error('自选读取失败'); },
    watchlistGroups: async () => ({ groups: [] }), indexDaily: async () => ({ rows: [] }),
  };
  const dispose = mountObserver(dom.window.document.querySelector('main'), { api });
  await new Promise(resolve => setTimeout(resolve, 30));
  const text = dom.window.document.querySelector('.lm').textContent;
  assert.match(text, /V0\.2\.2/);
  assert.match(text, /TSP行情同步进度/);
  assert.match(text, /非MA55扫描进度/);
  assert.match(text, /休市门控/);
  assert.match(text, /已发现 1 \/ 官方统计 3/);
  assert.match(text, /未安装/);
  assert.match(text, /真实已处理：未执行/);
  assert.match(text, /BLOCKED_BY_OFFICIAL_EXTENSION_CONTRACT/);
  assert.match(text, /自选读取失败/);
  dom.window.document.querySelector('[data-action="supply-reload"]').click();
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(snapshotCalls, 2);
  dispose(); dom.window.close();
});
test('unavailable statistics and failed package lookup never turn into a zero denominator or uninstalled claim', async () => {
  const api = supplyApi();
  api.dataStatus = async () => ({ instruments: null, stats_pending: true });
  api.extensionPackages = async () => { throw new Error('包状态不可读'); };
  const status = await loadSupplyStatus(api);
  assert.equal(status.stocks.data.symbols, null);
  assert.equal(status.backend.data, undefined);
});
