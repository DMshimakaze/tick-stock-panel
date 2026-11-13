import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { renderStatus, statusModel } from '../src/status-view.mjs';
import { mountResults, readDay } from '../src/results.mjs';
import { CSS } from '../src/styles.mjs';

const pause=()=>new Promise(resolve=>setTimeout(resolve,20));
const base={phase:'A',work_stage:'A',active_operation:'COLLECTING_NATIVE15',sync_mode:'LIVE_CATCHUP',
  worker_alive:true, monitored_stock_count:17,monitored_etf_count:3,
  data_target_15m:'2026-11-13 10:45:00',latest_saved_15m:'2026-11-13 10:30:00',
  latest_analyzed_15m:'2026-11-13 10:15:00',last_publish_at:'2026-11-13 10:51:02',
  today_session_verified:true,history_queue_count:12,exclusive:{factor_missing:7,warmup:3,missing_target:11,invalid_input:2},factor_retry:{pending:8,due:3,cooling:5},last_success_live:{at:'2026-11-13 10:51:00'},
  refresh:{refreshed:9,expected:20},analysis:{calculated:6,expected:20},
  latest_day_buy_count:4,latest_buy_date:'2026-11-13'};

test('published independent facts produce five colored KPIs and two in-card progress values',()=>{
  const dom=new JSDOM(renderStatus(base));
  const d=dom.window.document;
  assert.equal(d.querySelectorAll('.mr-kpi').length,5);
  assert.deepEqual([...d.querySelectorAll('[role=progressbar]')].map(e=>[e.getAttribute('aria-valuenow'),e.getAttribute('aria-valuemax')]),[['9','20'],['6','20']]);
  for(const text of ['MA55 选股','监控范围','最新已完成15m','当日K覆盖','可分析候选','当日候选数','股票 17','ETF 3','20','10:45','今日增量追赶','K已到但因子缺失 7','预热不足 3','源缺目标K 11','最近A/B成功 10:51','10:30','10:15']) assert.ok(d.body.textContent.includes(text),text);
  assert.ok(!d.querySelector('.mr-runtime-detail').open);
  assert.equal(d.querySelectorAll('.mr-runtime [role=progressbar]').length,0);
  assert.ok(!d.body.textContent.includes('7,661'));
  dom.window.close();
});

test('operation, history queue and sync mode remain independent',()=>{
  for(const [stage,text] of [['A','今日增量追赶'],['B','最低预热'],['F','补因子'],['C','深历史补齐']]){
    const dom=new JSDOM(renderStatus({...base,work_stage:stage}));
    assert.match(dom.window.document.body.textContent,new RegExp(text));
    assert.match(dom.window.document.body.textContent,/剩余历史队列12/);
    dom.window.close();
  }
  assert.equal(statusModel({...base,active_operation:'IDLE',sync_mode:'WAIT_NEXT_BAR'}).label,'等待下一根');
  assert.equal(statusModel({...base,phase:'HISTORY',sync_mode:'HISTORY_BACKFILL'}).label,'补齐历史');
  const lag=statusModel({...base,blocking_reason:'CALENDAR_LAG'});
  assert.equal(lag.tone,'warning'); assert.equal(lag.label,'数据滞后');
  assert.equal(statusModel({...base,phase:'ERROR',blocking_reason:'ERROR'}).tone,'danger');
});

test('missing facts never become zero, all-current, or a system-date trading day',()=>{
  const dom=new JSDOM(renderStatus({}));
  assert.match(dom.window.document.body.textContent,/尚未核实/);
  assert.ok(!dom.window.document.body.textContent.includes('已跟上'));
  assert.ok(!dom.window.document.body.textContent.includes('0 / 0'));
  assert.equal(dom.window.document.querySelector('[role=progressbar]').getAttribute('aria-valuetext'),'尚未核实');
  assert.match(CSS,/flex:0 1 260px/);
  assert.match(CSS,/repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(CSS,/"JetBrains Mono","IBM Plex Mono",ui-monospace,monospace/);
  assert.match(CSS,/font-family:inherit;font-size:13px/);
  dom.window.close();
  const unverified=new JSDOM(renderStatus({monitoring_verified:false,refresh:{refreshed:0,expected:0},analysis:{calculated:0,expected:0}}));
  assert.ok(!unverified.window.document.body.textContent.includes('0 / 0'));
  unverified.window.close();
});

test('publication failure retains previous rows, ETF precision, keyboard link and input focus',async(t)=>{
  const old={symbol:'159231.SZ',name:'合成ETF',market_date:'2026-11-12',generation:1,
    events_json:JSON.stringify([{event_id:'immutable',confirmed_at:'2026-11-12 11:15:00'}]),decision_json:'{}'};
  let s={...base,market_date:'2026-11-12',published_market_date:'2026-11-12',generation:1,revision:1,available_dates:['2026-11-12']};
  const calls=[];
  const api={extPagesList:async()=>[{enabled:true,path:'/lan-kline'}],extDataRows:async id=>{
    calls.push(id);
    if(id==='lan_ma55_native15_summary_v1')return{rows:[{payload_json:JSON.stringify(s)}]};
    if(id==='lan_ma55_day')return{rows:[old]};
    if(id==='lan_ma55_native15_prices_v1')return{rows:[{payload_json:JSON.stringify({day:'2026-11-12',rows:[{symbol:old.symbol,asset_type:'etf',price:.454,bar_end:'2026-11-12 15:00:00'}]})}]};
    throw Error('Unexpected non-projection call');
  }};
  const dom=new JSDOM('<main/>',{url:'http://localhost',pretendToBeVisual:true});
  const dispose=mountResults(dom.window.document.querySelector('main'),{api});
  t.after(()=>{dispose();dom.window.close();});
  await pause();
  const d=dom.window.document;
  assert.match(d.querySelector('tbody').textContent,/0.454/);
  assert.equal(new URL(d.querySelector('[data-stock-link]').href).searchParams.get('event_id'),'immutable');
  assert.equal(d.querySelector('[data-row]').tabIndex,0);
  const query=d.querySelector('[data-control=query]');query.focus();query.value='ETF';query.setSelectionRange(1,2);query.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  s={...s,market_date:'2026-11-13',revision:2,pending_publish:true,blocking_reason:'PUBLISH_FAILED',blocking_message:'发布失败，保留上次有效结果'};
  d.querySelector('[data-action=refresh]').click(); await pause();
  assert.equal(d.activeElement.dataset.control,'query');assert.equal(d.activeElement.selectionStart,1);
  assert.match(d.querySelector('tbody').textContent,/合成ETF/);
  assert.match(d.querySelector('h2').textContent,/上次有效交易日 2026-11-12 · 1 人工复核候选 · 新结果正在发布/);
  assert.equal(d.querySelectorAll('thead th').length,3);
  assert.ok(calls.every(id=>id.startsWith('lan_ma55_')));
  d.querySelector('[data-action=clear]').click();assert.equal(d.querySelector('[data-control=query]').value,'');
});

test('an empty latest-day projection remains publication-pending',async()=>{
  const info=await readDay({extDataRows:async()=>({rows:[]})},'2026-11-13',{market_date:'2026-11-13'});
  assert.equal(info.ready,false);
});

test('an old published heartbeat cannot pretend the backend is still collecting',()=>{
  const s={...base,status_at:'2026-11-13 10:52:00'};
  assert.equal(statusModel(s,Date.parse('2026-11-13T10:52:30+08:00')).stale,false);
  const stale=statusModel(s,Date.parse('2026-11-13T10:55:00+08:00'));
  assert.equal(stale.stale,true);assert.equal(stale.label,'状态已过期');
  assert.equal(stale.operation,'尚未收到新的后台状态');
});

test('historical quota wait keeps a live-source blockage visible',()=>{
  const dom=new JSDOM(renderStatus({...base,phase:'QUOTA_WAIT',work_stage:'C',active_operation:'IDLE',
    sync_mode:'HISTORY_BACKFILL',blocking_reason:'SOURCE_LAG',blocking_message:'部分证券未取得目标15m',wait_seconds:18}));
  assert.match(dom.window.document.body.textContent,/深历史补齐 · 等待额度/);
  assert.match(dom.window.document.body.textContent,/深历史使用余量，不阻挡当日候选/);
  assert.match(dom.window.document.body.textContent,/数据滞后/);
  dom.window.close();
});
