import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { mountWorkbench,parseLink } from '../src/page.mjs';
import extension from '../src/extension.mjs';
import { aggregateFive,stamp } from '../src/aggregate.mjs';
import { movingAverages } from '../src/indicators.mjs';
const pause=()=>new Promise(r=>setTimeout(r,20));
function data() {
 const calendar=[];let d=new Date('2026-07-01T00:00:00Z');
 while(calendar.length<75) {if(d.getUTCDay()>0 && d.getUTCDay()<6) calendar.push(d.toISOString().slice(0,10));d.setUTCDate(d.getUTCDate()+1);}
 return {symbol:'600519.SH',name:'合成测试股',asset_type:'stock',basis:'QFQ',basis_note:'测试投影',calendar,
 sessions:calendar.map(date=>({date,rows:[...Array.from({length:24},(_,i)=>575+i*5),...Array.from({length:24},(_,i)=>785+i*5)].map(m=>({date,datetime:stamp(date,m),open:10,high:12,low:9,close:11,volume:3,amount:3300}))})),
 oneSessions:[],daily:[],status:{phase:'READY',periods:{'5m':{days:75,bars:3600}}}};
}
function setup(){
 const dom=new JSDOM('<main/>',{url:'http://tsp/lan-kline?symbol=600519.SH&period=15m',pretendToBeVisual:true}), updates=[], requests=[], source=data();
 const chart={update:(m,reset)=>updates.push({m,reset}),range:n=>updates.push({range:n}),dates:(from,to)=>updates.push({from,to}),dispose(){}};
 const reader={async read(s,opts){requests.push(opts);return source;},async poll(){return null;},dispose(){}};
 const dispose=mountWorkbench(dom.window.document.querySelector('main'),{api:{instrumentSearch:async()=>({results:[{symbol:source.symbol,name:source.name,asset_type:'stock'}]})}},
 {chartFactory:()=>chart,readerFactory:()=>reader,now:()=> '2026-12-01 16:00:00'});
 const $=s=>dom.window.document.querySelector(s);
 return {dom,$,updates,requests,close(){dispose();dom.window.close();}};
}
test('75-day raw5m generates 1200 15m and 300 60m; both MA55/233 have real computed points',()=>{
 const d=data();for(const [period,n] of [['15m',1200],['60m',300]]) {
  const result=aggregateFive(d.sessions,period,'2026-12-01 16:00:00',d.calendar);
  assert.equal(result.validCount,n);assert.equal(result.continuous,n);
  const ma=movingAverages(result.bars,[55,233]);assert.equal(ma.get(233).filter(p=>p.value!==undefined).length,n-232);
  assert.equal(ma.get(233).at(-1).value,11);
 }
 const broken=structuredClone(d);broken.sessions.at(-1).rows.splice(2,1);
 const result=aggregateFive(broken.sessions,'15m','2026-12-01 16:00:00',d.calendar);
 assert.equal(result.gaps,1);assert.ok(result.continuous<55);
});
test('native-style compact controls, period persistence, custom MA, date range and refresh preserve view',async()=>{
 const s=setup();await pause();assert.equal(s.updates.at(-1).m.bars.length,1200);
 s.$('[data-period="60m"]').click();await pause();assert.equal(s.updates.at(-1).m.bars.length,300);
 s.$('[aria-label="显示MA5"]').click();assert.ok(s.updates.at(-1).m.averages.has(5));
 s.$('[aria-label="自定义可见根数"]').value='40';s.$('[data-action="range"]').click();assert.equal(s.updates.at(-1).range,40);
 const prefs=JSON.parse(s.dom.window.localStorage.getItem('lan.kline.preferences.v1'));
 assert.deepEqual(Object.keys(prefs).sort(),['mas','period','visible']);assert.equal(prefs.period,'60m');
 s.$('[aria-label="起始日期"]').value='2026-09-01';s.$('[aria-label="结束日期"]').value='2026-09-02';s.$('[data-action="dates"]').click();assert.ok(s.updates.at(-1).from<s.updates.at(-1).to);
 s.$('[data-action="refresh"]').click();await pause();assert.equal(s.updates.at(-1).reset,false);
 assert.ok(s.requests.every(r=>r.period));s.close();
});
test('strict event timestamp and manifest match unified extension',async()=>{
 assert.equal(extension.version,'1.1.2');assert.equal(extension.routes[0].path,'/lan-kline');
 assert.equal(parseLink('http://tsp/?symbol=000006.SZ&event=2026-11-06T15:00:00&setup=2026-11-06T14:30:00&defense=7.25').defense,7.25);
 assert.equal(parseLink('http://tsp/?event=2026-02-30T15:00:00').event,null);
 const built=await import('../dist/extension.js');assert.equal(built.default.version,'1.1.2');
});
