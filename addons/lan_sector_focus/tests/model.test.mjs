import test from 'node:test';
import assert from 'node:assert/strict';
import {membershipIndex,scoreIndustries,nativeCandidates,project,workbenchLink} from '../src/model.mjs';
import {fixture,config,field,dates,now,symbol} from './fixtures.mjs';

test('flat identity is exact; hyphens stay unresolved; slash and spaces are not split',()=>{
  const rows=[{symbol:symbol(1),[field]:'IT服务'}, {symbol:symbol(1),[field]:'IT服务'},
    {symbol:symbol(1),[field]:'能源 / 服务'}, {symbol:symbol(2),[field]:'软件开发-垂直应用'},
    {symbol:'000003',[field]:'银行'},{symbol:symbol(4),[field]:'汽车 服务'}];
  const m=membershipIndex({total:rows.length,rows},config);
  assert.deepEqual([...m.sectors.keys()],['IT服务','能源 / 服务','汽车 服务']);
  assert.equal(m.sectors.get('IT服务').size,1);
  assert.deepEqual(m.bySymbol.get(symbol(1)),['IT服务','能源 / 服务']);
  assert.equal(m.pending[0].raw_label,'软件开发-垂直应用');
  assert.equal(m.invalidSymbols.length,1);
  assert.match(m.version,/ext_hy_ths@2026-11-13/);
});

test('20 actual trading dates, hand-calculated persistence, recency, excess and three classes',()=>{
  const f=fixture(),m=membershipIndex(f.membership,f.config);
  const scores=scoreIndustries(f.rotation,m,f.market,f.now);
  assert.deepEqual(scores.dates,dates);
  const a=scores.rows.find(r=>r.name==='行业00'),b=scores.rows.find(r=>r.name==='行业23'),c=scores.rows.find(r=>r.name==='行业01');
  assert.equal(a.top10,20); assert.equal(a.top20,20); assert.equal(a.score,100);
  assert.equal(a.recent5,1); assert.equal(a.relativeMarket,.024);
  assert.equal(a.category,'持续强势'); assert.equal(b.category,'新晋走强'); assert.equal(c.category,'明显退潮');
  assert.equal(b.top10,5); assert.equal(b.valid,true);
  assert.equal(scores.benchmark.coverage,2);
});

test('incomplete current session excluded; missing sector dates never zero-filled',()=>{
  const f=fixture(); f.rotation.dates.unshift('2026-11-16');f.rotation.columns['2026-11-16']=f.rotation.columns[dates[0]];
  f.rotation.columns[dates[4]]=f.rotation.columns[dates[4]].filter(([name])=>name!=='行业00');
  const scores=scoreIndustries(f.rotation,membershipIndex(f.membership,f.config),f.market,'2026-11-16 14:59:59');
  assert.deepEqual(scores.dates,dates);
  const row=scores.rows.find(r=>r.name==='行业00');
  assert.equal(row.valid,false);assert.equal(row.status,'INSUFFICIENT_HISTORY');
  assert.equal(row.coverage,19/20);assert.deepEqual(row.missingDates,[dates[4]]);assert.equal(row.score,null);
});

test('stale or unknown snapshot time and incomplete SDK reads cannot be valid rankings',()=>{
  const f=fixture();
  for(const [change,expected] of [[{latest_sync_date:'2026-11-12 22:00:00'},'STALE_MEMBERSHIP'],
    [{latest_sync_date:null},'STALE_MEMBERSHIP']]) {
    const m=membershipIndex(f.membership,{...config,...change});
    assert.equal(scoreIndustries(f.rotation,m,f.market,now).rows[0].status,expected);
  }
  const m=membershipIndex({...f.membership,total:25},config);
  assert.equal(scoreIndustries(f.rotation,m,f.market,now).rows[0].status,'INCOMPLETE_MEMBERSHIP');
});

test('slash-name matrix mismatch and empty rotation stay unavailable, not a fake score',()=>{
  const m=membershipIndex({total:1,rows:[{symbol:symbol(1),[field]:'能源 / 服务'}]},config);
  const s=scoreIndustries({dates,columns:Object.fromEntries(dates.map(d=>[d,[['能源',.01],['服务',.02]]]))},m,{},now);
  assert.equal(s.rows[0].score,null);assert.equal(s.rows[0].missingDates.length,20);
  assert.equal(scoreIndustries({dates:[],columns:{}},m,{},now).coverage,0);
});

test('native event IDs and modes survive; duplicate symbols and independent events are distinct',()=>{
  const f=fixture();const second={...f.event,event_id:'native:test:2',confirmed_at:'2026-11-13 14:45:00',mode:'LIVE'};
  f.day.rows[0].events_json=JSON.stringify([f.event,second,f.event]);
  const c=nativeCandidates(f.summary,f.day,f.sources);
  assert.equal(c.events.length,2);assert.equal(c.bySymbol.size,1);
  assert.equal(c.events.find(e=>e.event_id==='native:test:1').mode,'REPLAY');
  assert.equal(c.rejected.length,0);assert.equal(c.events[0].confirmed_at,second.confirmed_at);
});

test('industry intersection does not label unmatched ETFs, mutate events or duplicate a stock',()=>{
  const f=fixture(),original=JSON.stringify(f);
  const view=project(f);
  assert.equal(view.intersection.size,1);assert.equal(view.candidates.events[0].event_id,f.event.event_id);
  assert.equal(JSON.stringify(f),original);
  f.day.rows[0].symbol='588920.SH';f.event.symbol='588920.SH';f.day.rows[0].events_json=JSON.stringify([f.event]);
  const unmatched=project(f);assert.equal(unmatched.intersection.size,0);assert.equal(unmatched.unmatched.length,1);
});

test('15m workbench URL preserves actual native provenance and exact confirmation',()=>{
  const f=fixture(),url=new URL(workbenchLink(f.event),'http://localhost');
  assert.equal(url.pathname,'/lan-kline');assert.equal(url.searchParams.get('period'),'15m');
  for(const key of ['event_id','rule_version','basis_id','factor_revision','basis_revision'])assert.equal(url.searchParams.get(key),String(f.event[key]));
  assert.equal(url.searchParams.get('source'),'TICKFLOW_NATIVE_15M');
  assert.equal(url.searchParams.get('event'),'2026-11-13T14:15:00');
});

test('a full 20-date matrix still cannot be current if a later completed local session is known',()=>{
  const f=fixture();f.market.as_of='2026-11-16';f.now='2026-11-16 15:30:00';
  const stale=project(f);
  assert.equal(stale.scores.rows[0].valid,false);assert.equal(stale.scores.rows[0].status,'STALE_RPS');
  assert.equal(stale.scores.rows[0].score,null);
  f.now='2026-11-16 14:30:00';
  assert.equal(project(f).scores.rows[0].valid,true,'current unfinished day must not displace the last completed session');
});

test('NO_EVENTS requires successful full same-day state rows with verified empty event arrays',()=>{
  const f=fixture();f.day.rows[0].events_json='[]';f.summary.latest_day_buy_count=0;
  const c=nativeCandidates(f.summary,f.day,f.sources);
  assert.equal(c.state,'NO_EVENTS');assert.equal(c.complete,true);assert.equal(c.eventCount,0);assert.equal(c.stockCount,0);
  f.day={total:0,rows:[]};
  const missing=nativeCandidates(f.summary,f.day,f.sources);
  assert.equal(missing.state,'SOURCE_UNAVAILABLE');assert.equal(missing.eventCount,null);
});

test('truncation, wrong date and forward generation never produce a current intersection',()=>{
  for(const [change,expected] of [
    [f=>f.day.total++,'TRUNCATED'],
    [f=>f.summary.published_market_date='2026-11-12','WRONG_DATE'],
    [f=>f.day.rows[0].market_date='2026-11-12','WRONG_DATE'],
    [f=>f.day.rows[0].generation=3,'GENERATION_MISMATCH'],
  ]) {
    const f=fixture();change(f);const v=project(f);
    assert.equal(v.candidates.state,expected);assert.equal(v.candidates.complete,false);
    assert.equal(v.candidates.eventCount,null);assert.equal(v.intersection.size,0);
  }
});

test('older generation retained confirmations stay valid under the actual changed-row publishing contract',()=>{
  const f=fixture();f.day.rows[0].generation=1;
  const c=nativeCandidates(f.summary,f.day,f.sources);
  assert.equal(c.complete,true);assert.equal(c.events[0].event_id,f.event.event_id);
  assert.equal(c.retainedOlderGenerationRows,1);
});

test('malformed events, unexpected family, pending publication and count conflicts cannot become NO_EVENTS',()=>{
  for(const change of [f=>f.day.rows[0].events_json='{',
    f=>f.day.rows[0].events_json=JSON.stringify([{...f.event,rule_version:'V5_R1'}]),
    f=>f.summary.pending_publish=true,f=>f.summary.latest_day_buy_count=2]) {
    const f=fixture();change(f);const c=nativeCandidates(f.summary,f.day,f.sources);
    assert.equal(c.complete,false);assert.equal(c.state,'SOURCE_UNAVAILABLE');assert.equal(c.eventCount,null);
  }
});
