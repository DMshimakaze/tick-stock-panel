import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { mountResults, readDay, workbenchUrl } from '../src/results.mjs';

const pause = () => new Promise(resolve => setTimeout(resolve, 20));
const summary = { market_date: '2026-11-06', data_asof: '2026-11-06 15:00:00',
  available_dates: ['2026-11-05', '2026-11-06'], generation: 1, revision: 4,
  backend_scanning: true, phase: 'WAITING_NEXT_SESSION', symbol_states: 2,
  requests: {minute_requests:9,period_15m:9,http_requests:15}, pending_symbols:1,
  refresh:{latest_completed15:'2026-11-06 15:00:00',refreshed:1,expected:2,generation:1,updating:true}, universe: { expected: 2 }, cycle: { expected_symbols: 2, processed_symbols: 2,
    valid_symbols: 1, insufficient_symbols: 1, gap_symbols: 0, failed_symbols: 0 } };
const row = { symbol: '600000.SH', name: '合成证券', generation: 1, market_date: '2026-11-06',
  phase: 'NO_SETUP', reason: 'AWAIT_SUPPORT_TEST', category: 'valid', bar_end: '2026-11-06 15:00:00',
  processed_at: '2026-11-08 12:00:00', events_json: '[]', decision_json: '{}' };
test('Sunday defaults to backend Friday, reports zero BUY and reads no watchlist, Kline or IndexedDB', async (t) => {
  const dom = new JSDOM('<main/>', { url: 'http://localhost', pretendToBeVisual: true });
  const calls = [], api = { extDataRows: async (id, options) => {
    calls.push([id, options]);
    if (id === 'lan_ma55_native15_summary_v1') return { rows: [{ payload_json: JSON.stringify(summary) }] };
    if (id === 'lan_ma55_day') return { rows: [row] };
    if (id === 'lan_ma55_native15_prices_v1') return {rows:[]};
    throw Error('Unexpected API');
  } };
  const dispose = mountResults(dom.window.document.querySelector('main'), { api });
  t.after(()=>{dispose();dom.window.close();});
  await pause();
  const text = dom.window.document.querySelector('.mr').textContent;
  assert.match(text, /最新交易日 2026-11-06 · 0 人工复核候选/);
  assert.match(text, /2026-11-06 15:00/);
  assert.match(text, /等待下一交易日/);
  assert.match(text, /1 \/ 2/);
  assert.equal(dom.window.document.querySelectorAll('.mr-kpi strong').length,5);
  assert.deepEqual([...dom.window.document.querySelectorAll('thead th')].map(e=>e.textContent),['名称','价格','确认时间']);
  assert.match(text, /工程原始状态/);
  assert.match(text, /56根连续合法15m/);
  assert.deepEqual(calls.map(c => c[0]), ['lan_ma55_native15_summary_v1','lan_ma55_native15_prices_v1', 'lan_ma55_day']);
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
  const api = { extDataRows: async id => ({ rows: id === 'lan_ma55_native15_summary_v1'
    ? [{ payload_json: JSON.stringify(summary) }] : id === 'lan_ma55_day' ? [row]
      : [{ payload_json: JSON.stringify({ days: { '2026-11-05': { cycle: summary.cycle, rows: [past] } } }) }] }) };
  const dispose = mountResults(dom.window.document.querySelector('main'), { api, ui: { openStockPreview: value => { preview = value; return true; } } });
  await pause();
  const date = dom.window.document.querySelector('[data-control="date"]'); date.value = '2026-11-05';
  date.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await pause();
  assert.match(dom.window.document.body.textContent, /历史交易日 2026-11-05 · 1 人工复核候选/);
  assert.match(dom.window.document.body.textContent, /历史重放/);
  dom.window.document.querySelector('[data-detail]').click();
  dom.window.document.querySelector('[data-action="preview"]').click();
  assert.equal(preview.symbol, '600000.SH');
  assert.match(dom.window.document.body.textContent, /2026-11-05 10:15:00/);
  dispose(); dom.window.close();
});
test('publication pending is distinct from a successfully scanned historical zero', async () => {
  const result = await readDay({ extDataRows: async () => ({ rows: [] }) }, '2026-11-05', summary);
  assert.equal(result.ready, false);
});

test('universe expansion retains same-day persisted BUY from the preceding generation',async()=>{
  const saved={...row,generation:1,events_json:JSON.stringify([{event_id:'immutable',confirmed_at:'2026-11-06 10:15:00'}])};
  const result=await readDay({extDataRows:async()=>({rows:[saved,{...saved,market_date:'2026-11-05'}]})},'2026-11-06',{...summary,generation:2});
  assert.equal(result.rows.length,1);
  assert.equal(JSON.parse(result.rows[0].events_json)[0].event_id,'immutable');
});

test('workbench link uses public enabled-page list, original symbol/time, and keeps official preview', async (t) => {
  const event={confirmed_at:'2026-11-06 13:15:00',mode:'REPLAY'};
  const url=new URL(workbenchUrl('600000.SH',event),'http://localhost');
  assert.equal(url.pathname,'/lan-kline');assert.equal(url.searchParams.get('symbol'),'600000.SH');
  assert.equal(url.searchParams.get('event'),'2026-11-06T13:15:00');
  for(const enabled of [true,false]){
    const dom=new JSDOM('<main/>',{url:'http://localhost',pretendToBeVisual:true});
    const api={extPagesList:async()=>[{id:'lan_kline',enabled,path:'/lan-kline',error:''}],
      extDataRows:async id=>({rows:id==='lan_ma55_native15_summary_v1'?[{payload_json:JSON.stringify(summary)}]:[{...row,events_json:JSON.stringify([event])}]})};
    const dispose=mountResults(dom.window.document.querySelector('main'),{api,ui:{openStockPreview:()=>true}});
    t.after(()=>{dispose();dom.window.close();});
    await pause();dom.window.document.querySelector('[data-detail]').click();
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
 assert.equal(extension.version,'0.9.8');assert.equal(extension.id,'lan.ma55');
 assert.equal(extension.apiVersion,1);assert.equal(extension.routes[0].path,'/lan-ma55');
 assert.equal(typeof built.routes[0].mount,'function');assert.equal(built.version,extension.version);
 const url=new URL(workbenchUrl('600000.SH',{event_id:'saved-id',rule_version:'saved-rule',confirmed_at:'2026-11-06 10:00:00'}),'http://local');
 assert.equal(url.searchParams.get('rule_version'),'saved-rule');
 assert.equal(url.searchParams.get('period'),'15m');
});

test('native candidates and frozen history use separate public projections and source links',async(t)=>{
  const dom=new JSDOM('<main/>',{url:'http://localhost',pretendToBeVisual:true});
  const nativeSummary={...summary,signal_family:'NATIVE15_REVIEW_V1',source_id:'TICKFLOW_NATIVE_15M'};
  const event={event_id:'NATIVE15_REVIEW_V1:case',rule_version:'NATIVE15_REVIEW_V1',source_id:'TICKFLOW_NATIVE_15M',
    source_count:1,confirmed_at:'2026-11-06 15:00:00',setup_at:'2026-11-06 14:30:00',mode:'REPLAY'};
  const nativeRow={...row,events_json:JSON.stringify([event])},calls=[];
  const api={extPagesList:async()=>[{enabled:true,path:'/lan-kline'}],extDataRows:async id=>{
    calls.push(id);
    if(id==='lan_ma55_native15_summary_v1')return{rows:[{payload_json:JSON.stringify(nativeSummary)}]};
    if(id==='lan_ma55_summary')return{rows:[{payload_json:JSON.stringify(summary)}]};
    if(id==='lan_ma55_native15_day_v1')return{rows:[nativeRow]};
    if(id==='lan_ma55_day')return{rows:[{...row,name:'旧冻结证券',events_json:JSON.stringify([{...event,event_id:'old',source_id:'TICKFLOW_RAW_5M'}])}]};
    return{rows:[]};
  }};
  const dispose=mountResults(dom.window.document.querySelector('main'),{api});
  t.after(()=>{dispose();dom.window.close();});await pause();
  const d=dom.window.document;
  assert.equal(d.querySelector('h1').textContent,'MA55 选股');
  assert.match(d.querySelector('.mr-subtitle').textContent,/原生15m.*人工复核候选/);
  assert.match(d.querySelector('tbody').textContent,/历史候选/);
  assert.equal(new URL(d.querySelector('[data-stock-link]').href).searchParams.get('source'),'TICKFLOW_NATIVE_15M');
  assert.ok(!calls.includes('lan_ma55_day'));
  d.querySelector('[data-tab=history]').click();
  assert.equal(d.querySelectorAll('[data-row]').length,0);
  await pause();
  assert.match(d.querySelector('tbody').textContent,/旧冻结证券/);
  assert.equal(new URL(d.querySelector('[data-stock-link]').href).searchParams.get('source'),'TICKFLOW_RAW_5M');
});
