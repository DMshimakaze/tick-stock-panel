import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { mountWorkbench,parseLink } from '../src/page.mjs';
import extension from '../src/extension.mjs';
import { stamp } from '../src/aggregate.mjs';
import { savedSeries,savedAverages } from '../src/data.mjs';
const pause=()=>new Promise(r=>setTimeout(r,20));
function data() {
 const calendar=[];let d=new Date('2026-07-01T00:00:00Z');
 while(calendar.length<75) {if(d.getUTCDay()>0 && d.getUTCDay()<6) calendar.push(d.toISOString().slice(0,10));d.setUTCDate(d.getUTCDate()+1);}
 const series=Object.fromEntries(['15m','30m','60m'].map(period=>{
  const size=Number(period.slice(0,-1)), times=calendar.flatMap(day=>[570,780].flatMap(base=>Array.from({length:120/size},(_,i)=>stamp(day,base+(i+1)*size)))).slice(-256);
  return [period,times.map((timestamp,i)=>({timestamp,open:10,high:12,low:9,close:11,volume:300,amount:3300,complete:true,adjust_factor:1,...Object.fromEntries([5,10,20,30,55,233].map(n=>['ma'+n,i>=n-1?11:null]))}))];
 }));
 return {symbol:'600519.SH',name:'合成测试股',asset_type:'stock',basis:'QFQ',basis_note:'公共测试视图',calendar,series,status:{phase:'READY',row_count:256,warm_count:256}};

}
function setup(){
 const dom=new JSDOM('<main/>',{url:'http://tsp/lan-kline?symbol=600519.SH&period=15m',pretendToBeVisual:true}), updates=[], requests=[], source=data();
 const chart={update:(m,reset)=>updates.push({m,reset}),range:n=>updates.push({range:n}),dates:(from,to)=>updates.push({from,to}),dispose(){}};
 const reader={async read(s,opts){requests.push(opts);return source;},async poll(){return null;},dispose(){}};
 const dispose=mountWorkbench(dom.window.document.querySelector('main'),{api:{instrumentSearch:async()=>{throw Error('Deep link must never search');}}},
 {chartFactory:()=>chart,readerFactory:()=>reader,now:()=> '2026-12-01 16:00:00'});
 const $=s=>dom.window.document.querySelector(s);
 return {dom,$,updates,requests,close(){dispose();dom.window.close();}};
}
test('saved periods and MAs are displayed without browser aggregation',()=>{
 const d=data();for(const period of ['15m','30m','60m']) {
  const result=savedSeries(d,period);assert.equal(result.validCount,256);
  const ma=savedAverages(result.bars,[55,233]);assert.equal(ma.get(233).filter(p=>p.value!==undefined).length,24);
  assert.equal(ma.get(233).at(-1).value,11);
 }
 const broken=structuredClone(d);broken.series['15m'].splice(250,1);
 assert.equal(savedSeries(broken,'15m').gaps,1);
});

test('native-style compact controls, period persistence, saved MA, date range and refresh preserve view',async(t)=>{
 const s=setup();t.after(()=>s.close());await pause();assert.equal(s.updates.at(-1).m.bars.length,256);
 s.$('[data-period="60m"]').click();await pause();assert.equal(s.updates.at(-1).m.bars.length,256);
 s.$('[aria-label="显示MA5"]').click();assert.ok(s.updates.at(-1).m.averages.has(5));
 s.$('[aria-label="自定义可见根数"]').value='40';s.$('[data-action="range"]').click();assert.equal(s.updates.at(-1).range,40);
 const prefs=JSON.parse(s.dom.window.localStorage.getItem('lan.kline.preferences.v1'));
 assert.deepEqual(Object.keys(prefs).sort(),['mas','period','visible']);assert.equal(prefs.period,'60m');
 s.$('[aria-label="起始日期"]').value='2026-10-01';s.$('[aria-label="结束日期"]').value='2026-10-02';s.$('[data-action="dates"]').click();assert.ok(s.updates.at(-1).from<s.updates.at(-1).to);
 s.$('[data-action="refresh"]').click();await pause();assert.equal(s.updates.at(-1).reset,false);
 assert.ok(s.requests.every(r=>r.period));s.close();
});
test('strict event timestamp and manifest match unified extension',async()=>{
 assert.equal(extension.version,'2.0.0');assert.equal(extension.routes[0].path,'/lan-kline');
 assert.equal(parseLink('http://tsp/?symbol=000006.SZ&event=2026-11-06T15:00:00&setup=2026-11-06T14:30:00&defense=7.25').defense,7.25);
 assert.equal(parseLink('http://tsp/?event=2026-02-30T15:00:00').event,null);
 const built=await import('../dist/extension.js');assert.equal(built.default.version,'2.0.0');
});
