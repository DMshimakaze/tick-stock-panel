import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { mountWorkbench,parseLink } from '../src/page.mjs';
import extension from '../src/extension.mjs';
import { A,B,apiFixture } from './fixtures.mjs';
const pause=()=>new Promise(r=>setTimeout(r,20));
function setup(params=`symbol=${A}&period=15m&event=2026-11-06T13:15:00`){
  const dom=new JSDOM('<main/>',{url:'http://tsp/lan-kline?'+params}), {api,calls}=apiFixture(), charts=[];
  let disposed=false;
  const chart={update:(model,reset)=>charts.push({model,reset}),range:n=>charts.push({range:n}),focus:()=>charts.push({focus:true}),dispose:()=>{disposed=true;}};
  const dispose=mountWorkbench(dom.window.document.querySelector('main'),{api},{chartFactory:()=>chart,now:()=> '2026-11-08 12:00:00'});
  const $=s=>dom.window.document.querySelector(s),change=(selector,value)=>{const e=$(selector);e.value=String(value);e.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
  return {dom,api,calls,charts,$,change,close(){dispose();assert.equal(disposed,true);dom.window.close();}};
}
test('static independent manifest and self-contained compiled artifact',async()=>{
  assert.equal(extension.id,'lan.kline'); assert.equal(extension.routes[0].path,'/lan-kline'); assert.equal(extension.navigation[0].label,'分钟 K 工作台');
  const built=await import('../dist/extension.js');assert.equal(built.default.id,extension.id);
  assert.equal(typeof built.default.routes[0].mount,'function');
});
test('Sunday deep link has 16 genuine 15m, MA shortfall, exact marker and default enabled 55/233',async()=>{
  const s=setup();assert.match(s.$('.kw-anchor').textContent,/尚未取得当前标的历史/);await pause();
  const model=s.charts.at(-1).model;
  assert.equal(model.bars.filter(b=>b.complete).length,16); assert.equal(model.anchor.index,8);
  assert.deepEqual([...model.averages.keys()],[55,233]); assert.match(s.$('.kw-status').textContent,/2026-11-06 15:00:00/);
  assert.match(s.$('.kw-availability').textContent,/MA233需要233根/);assert.match(s.$('.kw-availability').textContent,/16根/);
  assert.equal(s.$('[data-action="tail"]').disabled,true);
  s.$('[data-action="focus"]').click();assert.equal(s.charts.at(-1).focus,true);s.close();
});
test('periods, custom MA edits/toggles/delete, visible roots reuse inputs and persist only preferences',async()=>{
  const s=setup();await pause();
  for(const p of ['1m','5m','30m','60m','1d','15m']) s.change('[aria-label="K线级别"]',p);
  assert.equal(s.calls.filter(c=>c[0]==='range').length,1);assert.equal(s.calls.filter(c=>c[0]==='daily').length,1);
  s.$('[aria-label="显示MA5"]').click(); assert.ok(s.charts.at(-1).model.averages.has(5));
  s.change('[aria-label="MA55周期"]',56);assert.ok(s.charts.at(-1).model.averages.has(56));
  s.$('[data-action="add-ma"]').click();assert.ok(s.charts.at(-1).model.averages.has(21));
  s.$('[aria-label="删除MA233"]').click();assert.ok(!s.charts.at(-1).model.averages.has(233));
  s.$('[aria-label="自定义可见根数"]').value='40';s.$('[data-action="range"]').click();assert.equal(s.charts.at(-1).range,40);
  const p=JSON.parse(s.dom.window.localStorage.getItem('lan.kline.preferences.v1'));assert.deepEqual(Object.keys(p).sort(),['mas','visible']);
  assert.equal(p.visible,40);s.close();
});
test('switch to ETF clears another symbols event; range boundary is explicit',async()=>{
  const s=setup('symbol='+A+'&event=2026-10-01T15:00:00');await pause();
  assert.match(s.$('.kw-anchor').textContent,/目标时点超出当前可得分钟历史/);
  s.$('[aria-label="股票或ETF搜索"]').value=B;s.$('form').dispatchEvent(new s.dom.window.Event('submit',{bubbles:true,cancelable:true}));await pause();
  const model=s.charts.at(-1).model;assert.equal(model.asset,'etf');assert.equal(model.anchor.index,-1);
  assert.equal(s.$('.kw-anchor').textContent,'');assert.equal(new URL(s.dom.window.location.href).searchParams.get('symbol'),B);s.close();
});
test('refresh failure keeps this symbols last valid input and reports the error',async()=>{
  const s=setup();await pause();s.api.klineMinuteRange=async()=>{throw Error('synthetic disconnected');};
  s.$('[data-action="refresh"]').click();await pause();
  assert.equal(s.charts.at(-1).model.bars.filter(b=>b.complete).length,16);
  assert.match(s.$('[role="alert"]').textContent,/保留上次有效输入/);s.close();
});
test('empty data and invalid URL/search are explicit without requesting a provider',async()=>{
  const s=setup('symbol=bad&period=bad&event=bad');await pause();
  assert.equal(s.calls.length,0);assert.match(s.$('[role="alert"]').textContent,/完整代码/);
  assert.equal(parseLink('http://tsp/lan-kline?event=2026-02-30T15:00:00').event,null);
  s.$('[aria-label="股票或ETF搜索"]').value=A;s.api.klineMinuteRange=async symbol=>({symbol,asset_type:'stock',source:'none',sessions:[]});
  s.$('form').dispatchEvent(new s.dom.window.Event('submit',{bubbles:true,cancelable:true}));await pause();
  assert.match(s.$('.kw-message').textContent,/没有可绘制的完整K/);s.close();
});

test('rapid page selection cannot display old stocks response or retain its event',async()=>{
  const dom=new JSDOM('<main/>',{url:'http://tsp/lan-kline'}),{api}=apiFixture(),models=[];let release;
  const original=api.klineMinuteRange;
  api.klineMinuteRange=(symbol,days)=>symbol===A?new Promise(resolve=>{release=()=>original(symbol,days).then(resolve);}):original(symbol,days);
  const dispose=mountWorkbench(dom.window.document.querySelector('main'),{api},{now:()=> '2026-11-08 12:00:00',chartFactory:()=>({update:m=>models.push(m),dispose(){},range(){},focus(){}})});
  async function select(symbol){dom.window.document.querySelector('input[aria-label="股票或ETF搜索"]').value=symbol;dom.window.document.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await pause();}
  await select(A);await select(B);release();await pause();
  assert.match(dom.window.document.querySelector('.kw-status').textContent,/159001.SZ/);
  assert.equal(models.at(-1).asset,'etf');assert.equal(models.at(-1).bars[0].open,200);
  dispose();dom.window.close();
});
