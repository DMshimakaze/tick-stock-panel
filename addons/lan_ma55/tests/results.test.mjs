import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { mountResults, readDay, workbenchUrl } from '../src/results.mjs';

const pause = () => new Promise(resolve => setTimeout(resolve, 20));
const summary = { market_date: '2026-11-06', data_asof: '2026-11-06 15:00:00',
  available_dates: ['2026-11-05', '2026-11-06'], generation: 1, revision: 4,
  backend_scanning: true, phase: 'WAITING_NEXT_SESSION', symbol_states: 2,
  requests: {minute_requests:9,http_requests:15}, pending_symbols:1,
  universe: { expected: 2 }, cycle: { expected_symbols: 2, processed_symbols: 2,
    valid_symbols: 1, insufficient_symbols: 1, gap_symbols: 0, failed_symbols: 0 } };
const row = { symbol: '600000.SH', name: '合成证券', generation: 1, market_date: '2026-11-06',
  phase: 'NO_SETUP', reason: 'AWAIT_SUPPORT_TEST', category: 'valid', bar_end: '2026-11-06 15:00:00',
  processed_at: '2026-11-08 12:00:00', events_json: '[]', decision_json: '{}' };
test('Sunday defaults to backend Friday, reports zero BUY and reads no watchlist, Kline or IndexedDB', async () => {
  const dom = new JSDOM('<main/>', { url: 'http://localhost', pretendToBeVisual: true });
  const calls = [], api = { extDataRows: async (id, options) => {
    calls.push([id, options]);
    if (id === 'lan_ma55_summary') return { rows: [{ payload_json: JSON.stringify(summary) }] };
    if (id === 'lan_ma55_day') return { rows: [row] };
    throw Error('Unexpected API');
  } };
  const dispose = mountResults(dom.window.document.querySelector('main'), { api });
  await pause();
  const text = dom.window.document.querySelector('.mr').textContent;
  assert.match(text, /最新交易日 2026-11-06 · 0 BUY/);
  assert.match(text, /2026-11-06 15:00:00/);
  assert.match(text, /等待下一交易日/);
  assert.match(text, /2 \/ 2/);
  assert.match(text, /本次启动5m请求 9 \/ HTTP 15/);
  assert.match(text, /待预热 1/);
  assert.deepEqual(calls.map(c => c[0]), ['lan_ma55_summary', 'lan_ma55_day']);
  dom.window.document.querySelector('[data-action="tab"][data-tab="data"]').click();
  assert.match(dom.window.document.body.textContent, /合成证券/);
  dispose(); assert.equal(dom.window.document.querySelector('.mr'), null); dom.window.close();
});
test('historical signals keep original date and REPLAY source; stock preview uses the host bridge', async () => {
  const dom = new JSDOM('<main/>', { url: 'http://localhost', pretendToBeVisual: true });
  const event = { confirmed_at: '2026-11-05 10:15:00', setup_at: '2026-11-05 09:45:00', mode: 'REPLAY',
    first_seen_at: '2026-11-08 12:00:00', pattern: 'MULTI_BAR', defense: 99, decision: {} };
  const past = { ...row, market_date: '2026-11-05', events_json: JSON.stringify([event]) };
  let preview;
  const api = { extDataRows: async id => ({ rows: id === 'lan_ma55_summary'
    ? [{ payload_json: JSON.stringify(summary) }] : id === 'lan_ma55_day' ? [row]
      : [{ payload_json: JSON.stringify({ days: { '2026-11-05': { cycle: summary.cycle, rows: [past] } } }) }] }) };
  const dispose = mountResults(dom.window.document.querySelector('main'), { api, ui: { openStockPreview: value => { preview = value; return true; } } });
  await pause();
  const date = dom.window.document.querySelector('[data-control="date"]'); date.value = '2026-11-05';
  date.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await pause();
  assert.match(dom.window.document.body.textContent, /历史交易日 2026-11-05 · 1 BUY/);
  assert.match(dom.window.document.body.textContent, /历史重放/);
  dom.window.document.querySelector('[data-row]').click();
  dom.window.document.querySelector('[data-action="preview"]').click();
  assert.equal(preview.symbol, '600000.SH');
  assert.match(dom.window.document.body.textContent, /2026-11-05 10:15:00/);
  dispose(); dom.window.close();
});
test('publication pending is distinct from a successfully scanned historical zero', async () => {
  const result = await readDay({ extDataRows: async () => ({ rows: [] }) }, '2026-11-05', summary);
  assert.equal(result.ready, false);
});

test('workbench link uses public enabled-page list, original symbol/time, and keeps official preview', async (t) => {
  const event={confirmed_at:'2026-11-06 13:15:00',mode:'REPLAY'};
  const url=new URL(workbenchUrl('600000.SH',event),'http://localhost');
  assert.equal(url.pathname,'/lan-kline');assert.equal(url.searchParams.get('symbol'),'600000.SH');
  assert.equal(url.searchParams.get('event'),'2026-11-06T13:15:00');
  for(const enabled of [true,false]){
    const dom=new JSDOM('<main/>',{url:'http://localhost',pretendToBeVisual:true});
    const api={extPagesList:async()=>[{id:'lan_kline',enabled,path:'/lan-kline',error:''}],
      extDataRows:async id=>({rows:id==='lan_ma55_summary'?[{payload_json:JSON.stringify(summary)}]:[{...row,events_json:JSON.stringify([event])}]})};
    const dispose=mountResults(dom.window.document.querySelector('main'),{api,ui:{openStockPreview:()=>true}});
    t.after(()=>{dispose();dom.window.close();});
    await pause();dom.window.document.querySelector('[data-row]').click();
    assert.ok(dom.window.document.querySelector('a[data-stock-link]'));
    assert.equal(Boolean(dom.window.document.querySelector('.mr-selected a.mr-kline')),enabled);
    assert.ok(dom.window.document.querySelector('[data-action="preview"]'));
    if(enabled)assert.equal(new URL(dom.window.document.querySelector('a.mr-kline').href).searchParams.get('event'),'2026-11-06T13:15:00');
    else assert.match(dom.window.document.body.textContent,/安装并启用独立分钟K工作台/);
    dispose();dom.window.close();
  }
});


test('only the published-results page is registered in the installable manifest',async()=>{
 const extension=(await import('../src/extension.mjs')).default;
 const built=(await import('../dist/extension.js')).default;
 assert.equal(extension.version,'0.5.2');assert.equal(extension.id,'lan.ma55');
 assert.equal(extension.apiVersion,1);assert.equal(extension.routes[0].path,'/lan-ma55');
 assert.equal(typeof built.routes[0].mount,'function');assert.equal(built.version,extension.version);
 const url=new URL(workbenchUrl('600000.SH',{event_id:'saved-id',rule_version:'saved-rule',confirmed_at:'2026-11-06 10:00:00'}),'http://local');
 assert.equal(url.searchParams.get('rule_version'),'saved-rule');
 assert.equal(url.searchParams.get('period'),'15m');
});
