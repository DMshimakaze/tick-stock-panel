import test from 'node:test';
import assert from 'node:assert/strict';
import {performance} from 'node:perf_hooks';
import {JSDOM} from 'jsdom';
import {ProjectionModel,resolveThemes,R1,V5} from '../src/model.mjs';
import {mountTheme} from '../src/page.mjs';
import {largeFixture} from './scale-fixture.mjs';
import {rows,themes,status,summary,date} from './fixtures.mjs';

test('saved unresolved/legacy bare mappings never change with date or SH/SZ candidates',()=>{
  const input=structuredClone(themes);
  const a=input[0].memberships[0];a.symbol='000001.SZ';a.symbol_status='UNRESOLVED_SYMBOL';
  const legacy=input[0].memberships[1];delete legacy.symbol_resolution;
  const today=resolveThemes(input,['000001.SZ']);
  const yesterday=resolveThemes(input,['000001.SH','000001.SZ']);
  assert.deepEqual(today,yesterday);
  assert.equal(today[0].memberships[0].symbol,null);assert.equal(today[0].memberships[1].symbol,null);
  const cache=new ProjectionModel();cache.update({status,themes:input,summary,rows,date});
  const tree=cache.tree;
  const historical=rows.map(r=>({...r,market_date:'2026-11-05',events_json:'[]'}));
  cache.update({status,themes:input,summary,rows:historical,date:'2026-11-05'});
  assert.strictEqual(cache.tree,tree);assert.equal(cache.tree.themes[0].memberships[0].symbol,null);
});

test('snapshot/day/revision caches invalidate atomically and reuse filter views',()=>{
  const cache=new ProjectionModel();cache.update({status,themes,summary,rows,date});
  const tree=cache.tree,day=cache.day,view=cache.view();
  cache.select(view.items,{themeId:'T1',branch:'l2'});cache.select(view.items,{scope:'unlabelled',query:'DEMO'});
  assert.strictEqual(cache.view(),view);assert.strictEqual(cache.tree,tree);assert.strictEqual(cache.day,day);
  cache.view(V5);assert.equal(cache.views.size,2);
  cache.update({status,themes,summary:{...summary,revision:2},rows,date});
  assert.strictEqual(cache.tree,tree);assert.notStrictEqual(cache.day,day);assert.equal(cache.views.size,0);
  const stable=cache.tree;
  assert.throws(()=>cache.update({status:{...status,snapshot_id:'bad'},themes,summary,rows:[{...rows[0],events_json:'bad'}],date}));
  assert.strictEqual(cache.tree,stable,'invalid day cannot publish the candidate tree');
  cache.update({status:{...status,snapshot_id:'s2'},themes:themes.slice(0,1),summary,rows,date});
  assert.equal(cache.tree.themes.length,1);assert.notStrictEqual(cache.tree,stable);
});

test('DEMO 300 themes / 1500 nodes / 30000 relations / 6000 events benchmark',async()=>{
  const fixture=largeFixture(),cache=new ProjectionModel();
  const begin=performance.now();cache.update(fixture);const view=cache.view();const initialMs=performance.now()-begin;
  assert.equal(view.stats.dayEvents,5400);assert.equal(view.stats.uniqueStocks,5400);
  const tree=cache.tree,day=cache.day,times=[];
  for(let n=0;n<300;n++) {
    const start=performance.now();const selected=cache.select(view.items,{themeId:'DEMO-T'+n,query:n%2?'DEMO':''});
    assert.ok(selected.length<=100);times.push(performance.now()-start);
    assert.strictEqual(cache.view(),view);
  }
  assert.strictEqual(cache.tree,tree);assert.strictEqual(cache.day,day);
  const dom=new JSDOM('<main></main>'),root=dom.window.document.querySelector('main');let calls=0;
  const tables={lan_theme_status:[{payload_json:JSON.stringify(fixture.status)}],lan_ma55_summary:[{payload_json:JSON.stringify(fixture.summary)}],lan_theme_tree:fixture.treeRows,lan_ma55_day:fixture.rows};
  const ctx={api:{extPagesList:async()=>[],extDataRows:async id=>{calls++;return {rows:tables[id],total:tables[id].length};}}};
  const domStart=performance.now(),stop=mountTheme(root,ctx);await new Promise(r=>setImmediate(r));
  const domInitialMs=performance.now()-domStart;
  assert.equal(root.querySelectorAll('[data-theme]').length,301);
  const before=calls,uiStart=performance.now();root.querySelector('[data-theme="DEMO-T120"]').click();
  const switchMs=performance.now()-uiStart;
  const next=root.querySelector('[data-action="next"]');if(!next.disabled)next.click();
  const input=root.querySelector('[data-control="stockQuery"]');input.value='DEMO';input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  assert.equal(calls,before);assert.ok(root.querySelectorAll('tbody tr').length<=40);
  times.sort((a,b)=>a-b);
  console.log('DEMO_SCALE_MEASUREMENTS '+JSON.stringify({themes:300,nodes:1500,relations:30000,events:6000,modelInitialMs:initialMs,filterP50Ms:times[150],filterMaxMs:times.at(-1),jsdomInitialMs:domInitialMs,jsdomThemeSwitchMs:switchMs,rssMiB:process.memoryUsage().rss/1048576,heapMiB:process.memoryUsage().heapUsed/1048576,extraApiCalls:calls-before}));
  stop();dom.window.close();
});
