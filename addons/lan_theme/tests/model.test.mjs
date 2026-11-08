import test from 'node:test';
import assert from 'node:assert/strict';
import * as m from '../src/model.mjs';
import {rows,themes,status,treeRows,summary,date,sdk} from './fixtures.mjs';

test('double-bank snapshot assembles all parts and rejects truncation',()=>{
  assert.equal(m.assembleThemes(status,[...treeRows,{...treeRows[0],bank:'B',snapshot_id:'old'}]).length,2);
  assert.throws(()=>m.assembleThemes(status,treeRows.slice(1)),/完整/);
  assert.throws(()=>m.assembleThemes({...status,parts_total:3},[...treeRows,treeRows[0]]),/分片/);
});
test('20000 public row limit never silently truncates',async()=>{
  await assert.rejects(m.readRows({extDataRows:async()=>({total:20001,rows:[]})},'lan_ma55_day',20000),/上限/);
});
test('canonical mapping never guesses ambiguous exchange from a name',()=>{
  const out=m.resolveThemes(themes,['000001.SZ','000001.SH','300863.SZ']);
  assert.equal(out[0].memberships[0].symbol,null);
  assert.equal(out[0].memberships[1].symbol,'300863.SZ');
  assert.equal(out[1].memberships[0].symbol,'000001.SZ');
});
test('R1 and V5, date and LIVE DELAYED REPLAY stay separate; event facts unchanged',()=>{
  const buys=m.confirmed([...rows,rows[0]],date);
  assert.equal(buys.length,4);
  assert.equal(m.confirmed(rows,date,{rule:m.V5}).length,1);
  for(const mode of ['LIVE','DELAYED','REPLAY'])assert.equal(m.confirmed(rows,date,{mode}).length,mode==='REPLAY'?2:1);
  assert.equal(m.confirmed(rows,'2026-11-05').length,0);
  assert.deepEqual(buys.find(i=>i.event.event_id==='a').event,JSON.parse(rows[0].events_json)[0]);
  assert.equal(m.watching(rows,date,summary).length,1);
  assert.equal(m.watching(rows,date,{market_date:'2026-11-09'})[0].watch_rule,null);
});
test('multi-theme and multi-branch membership does not inflate unique stocks',()=>{
  const resolved=m.resolveThemes(themes,rows.map(r=>r.symbol)),index=m.indexThemes(resolved),buys=m.confirmed(rows,date);
  assert.equal(index.get('300863.SZ').length,2);
  assert.equal(index.get('000001.SZ').length,2);
  assert.deepEqual(m.themeCounts(resolved[0],buys),{members:3,resolved:3,hits:2});
  assert.equal(m.themeCounts(resolved[0],buys,'l1').hits,2);
  assert.equal(m.selectItems(buys,index,{themeId:'T1',branch:'l2'}).length,1);
  assert.equal(m.selectItems(buys,index,{scope:'unlabelled'})[0].symbol,'600000.SH');
  assert.deepEqual(m.statistics(summary,buys,m.watching(rows,date,summary),index),{savedEvents:100,dayEvents:4,uniqueStocks:3,replayEvents:2,liveEvents:1,delayedEvents:1,watchStocks:1,labelledStocks:2});
});
test('300863 historical deep link keeps exact 15m event setup defense factor',()=>{
  const e=JSON.parse(rows[1].events_json)[0],url=new URL(m.workbenchLink('300863.SZ',e),'http://localhost');
  assert.equal(url.pathname,'/lan-kline');assert.equal(url.searchParams.get('period'),'15m');
  for(const [k,v] of Object.entries({symbol:'300863.SZ',event:e.confirmed_at.replace(' ','T'),setup:e.setup_at.replace(' ','T'),defense:String(e.defense),factor:String(e.adjust_factor)}))assert.equal(url.searchParams.get(k),v);
});
test('historical projection is cached and missing date fails visibly',async()=>{
  const ctx=sdk(),cache={value:null};await m.readDay(ctx.api,'2026-11-05',summary,cache);
  await m.readDay(ctx.api,'2026-11-05',summary,cache);
  assert.equal(ctx.calls.filter(c=>c[0]==='lan_ma55_history').length,1);
  await assert.rejects(m.readDay(ctx.api,'2026-11-04',summary,cache),/尚无/);
});
