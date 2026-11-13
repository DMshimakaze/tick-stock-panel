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
  const size=Number(period.slice(0,-1)), times=calendar.flatMap(day=>[570,780].flatMap(base=>Array.from({length:120/size},(_,i)=>stamp(day,base+(i+1)*size)))).slice(-({'15m':250,'30m':125,'60m':62}[period]));
  return [period,times.map((timestamp,i)=>({timestamp,open:10,high:12,low:9,close:11,volume:300,amount:3300,complete:true,adjust_factor:1,...Object.fromEntries([5,10,20,30,55,233].map(n=>['ma'+n,i>=n-1?11:null]))}))];
 }));
 return {symbol:'600519.SH',name:'合成测试股',asset_type:'stock',basis:'QFQ',basis_note:'公共测试视图',calendar,series,status:{phase:'READY',row_count:250,warm_count:250,capacity:250}};

}
function setup(source=data(),url='http://tsp/lan-kline?symbol=600519.SH&period=15m',prefs=null,key='lan.kline.preferences.v1'){
 const dom=new JSDOM('<main/>',{url,pretendToBeVisual:true}), updates=[], requests=[],visibility=[];
 if(prefs!==null) dom.window.localStorage.setItem(key,typeof prefs==='string'?prefs:JSON.stringify(prefs));
 let onReadout;
 const chart={update:(m,reset)=>{updates.push({m,reset});const bar=m.bars.findLast(b=>b.complete);
   if(bar)onReadout({...bar,averages:[...m.averages].map(([n,points])=>[n,points.find(p=>p.time===bar.time)?.value])});},
   setMaVisible:ma=>visibility.push({...ma}),range:n=>updates.push({range:n}),focus:()=>updates.push({focus:true}),dates:(from,to)=>updates.push({from,to}),dispose(){}};
 const reader={async read(s,opts){requests.push(opts);return source;},async poll(){return null;},dispose(){}};
 const api={instrumentSearch:async()=>{throw Error('Deep link must never search');}};
 const dispose=mountWorkbench(dom.window.document.querySelector('main'),{api},
 {chartFactory:(_,readout)=>{onReadout=readout;return chart;},readerFactory:()=>reader,now:()=> '2026-12-01 16:00:00'});
 const $=s=>dom.window.document.querySelector(s);
 return {dom,$,updates,requests,reader,visibility,api,readout:b=>onReadout(b),close(){dispose();dom.window.close();}};
}
test('saved periods and MAs are displayed without browser aggregation',()=>{
 const d=data();for(const period of ['15m','30m','60m']) {
  const result=savedSeries(d,period);assert.equal(result.validCount,({'15m':250,'30m':125,'60m':62}[period]));
  const ma=savedAverages(result.bars,[55,233]);assert.equal(ma.get(233).filter(p=>p.value!==undefined).length,period==='15m'?18:0);
  assert.equal(ma.get(233).at(-1).value,period==='15m'?11:undefined);
 }
 const broken=structuredClone(d);broken.series['15m'].splice(244,1);
 assert.equal(savedSeries(broken,'15m').gaps,1);
});

test('native-style compact controls, period persistence, saved MA, date range and refresh preserve view',async(t)=>{
 const s=setup();t.after(()=>s.close());await pause();assert.equal(s.updates.at(-1).m.bars.length,250);
 s.$('[data-period="60m"]').click();await pause();assert.equal(s.updates.at(-1).m.bars.length,62);
 assert.ok(s.updates.at(-1).m.averages.get(233).every(p=>p.value===undefined));assert.equal(s.$('[data-ma-value="233"]').textContent,'—');
 assert.equal(s.$('[aria-label="显示MA5"]').checked,true);assert.equal(s.$('[aria-label="显示MA233"]').checked,true);
 s.$('[aria-label="自定义可见根数"]').value='40';s.$('[data-action="range"]').click();assert.equal(s.updates.at(-1).range,40);
 const prefs=JSON.parse(s.dom.window.localStorage.getItem('lan.kline.preferences.v2'));
 assert.deepEqual(Object.keys(prefs).sort(),['maVisible','period','visible']);assert.equal(prefs.period,'60m');
 s.$('[aria-label="起始日期"]').value='2026-10-01';s.$('[aria-label="结束日期"]').value='2026-10-02';s.$('[data-action="dates"]').click();assert.ok(s.updates.at(-1).from<s.updates.at(-1).to);
 s.$('[data-action="refresh"]').click();await pause();assert.equal(s.updates.at(-1).reset,false);
 assert.ok(s.requests.every(r=>r.period));s.close();
});
test('strict event timestamp and manifest match unified extension',async()=>{
 assert.equal(extension.version,'3.3.0');assert.equal(extension.routes[0].path,'/lan-kline');
 assert.equal(parseLink('http://tsp/?symbol=000006.SZ&event=2026-11-06T15:00:00&setup=2026-11-06T14:30:00&defense=7.25').defense,7.25);
 assert.equal(parseLink('http://tsp/?event=2026-02-30T15:00:00').event,null);
 const built=await import('../dist/extension.js');assert.equal(built.default.version,'3.3.0');
});

test('empty 503 is a service failure with a status link, never ordinary warmup',async(t)=>{
 const s=setup({symbol:'600519.SH',unavailable:true,status:{phase:'DATA_SOURCE_UNAVAILABLE',error:'公共行情不可用 HTTP 503 · ERROR (SQLITE_READ_ERROR)'}});
 t.after(()=>s.close());await pause();
 assert.match(s.$('.kw-readout').textContent,/服务不可用/);
 assert.doesNotMatch(s.$('.kw-readout').textContent,/预热真实历史/);
 assert.match(s.$('.kw-status').textContent,/503.*SQLITE_READ_ERROR/);
 assert.equal(s.$('.kw-source-status').hidden,false);
 assert.equal(s.$('.kw-source-status').getAttribute('href'),'/api/extensions/lan.ma55/v1/status');
});

test('partial saved 15m draws immediately and an empty 60m retains the labeled 15m chart',async(t)=>{
 const source=data();source.series['15m']=source.series['15m'].slice(-160);source.series['60m']=[];
 source.status={phase:'WARMING',row_count:160,warm_count:0};
 const s=setup(source);t.after(()=>s.close());await pause();
 assert.equal(s.updates.at(-1).m.bars.length,160);
 assert.match(s.$('.kw-coverage').textContent,/160根 15m/);
 s.$('[data-period="60m"]').click();await pause();
 assert.equal(s.updates.at(-1).m.bars.length,160);
 assert.match(s.$('.kw-coverage').textContent,/160根 15m/);
 assert.match(s.$('.kw-availability').textContent,/60m尚无已完成K.*保留15m/);
 assert.equal(s.updates.at(-1).reset,false);
});

test('a source error preserves valid bars and a successful retry clears the error',async(t)=>{
 const source=data(),s=setup(source);t.after(()=>s.close());await pause();
 s.reader.read=async()=>({symbol:source.symbol,unavailable:true,status:{phase:'DATA_SOURCE_UNAVAILABLE',error:'HTTP 503'}});
 s.$('[data-action="refresh"]').click();await pause();
 assert.equal(s.updates.at(-1).m.bars.length,250);
 assert.match(s.$('.kw-status').textContent,/保留上次有效图表/);
 s.reader.read=async()=>source;s.$('[data-action="refresh"]').click();await pause();
 assert.equal(s.$('[role="alert"]').textContent,'');
 assert.equal(s.$('.kw-source-status').hidden,true);
});


test('event identity, source and support stay in the deep link without fixed lines or long metadata',async(t)=>{
 const source=data();source.source_id='TICKFLOW_RAW_5M';source.basis_revision=1;
 const end=source.series['15m'].at(-1).timestamp,setupAt=source.series['15m'].at(-2).timestamp;
 const q=new URLSearchParams({symbol:source.symbol,period:'15m',event:end,setup:setupAt,event_id:'frozen-event-id',source:'TICKFLOW_RAW_5M',factor:'1',basis_revision:'1',defense:'9.25',risk:'原风险信息'});
 const s=setup(source,'http://tsp/lan-kline?'+q);t.after(()=>s.close());await pause();
 assert.equal(new URL(s.dom.window.location.href).searchParams.get('event_id'),'frozen-event-id');
 assert.equal(new URL(s.dom.window.location.href).searchParams.get('defense'),'9.25');
 assert.equal(new URL(s.dom.window.location.href).searchParams.get('setup'),setupAt.replace(' ','T'));
 assert.equal(new URL(s.dom.window.location.href).searchParams.get('source'),'TICKFLOW_RAW_5M');
 assert.equal(s.updates.at(-1).m.defense,undefined);assert.ok(s.updates.at(-1).m.anchor.index>=0);
 assert.match(s.$('.kw-anchor').textContent,/15m BUY.*2026/);
 assert.doesNotMatch(s.dom.window.document.body.textContent,/frozen-event-id|原风险信息|支撑测试|防守/);
 s.reader.read=async()=>({...source,correction:true,basis_revision:2});
 s.$('[data-action="refresh"]').click();await pause();
 assert.equal(s.updates.at(-1).m.defense,undefined);assert.match(s.$('.kw-status').textContent,/复权基准未核实/);
 assert.equal(new URL(s.dom.window.location.href).searchParams.get('event_id'),'frozen-event-id');
});


test('valid legacy MA choices migrate while deep links keep every historical BUY',async(t)=>{
 const source=data(),bars=source.series['15m'];
 source.events={rows:[bars.at(-12),bars.at(-8),bars.at(-1)].map((b,i)=>({payload:{event_id:'old-'+i,confirmed_at:b.timestamp,setup_at:bars.at(-14).timestamp,defense:8,rule_version:'saved-rule'}})),total:3,truncated:false};
 const s=setup(source,'http://tsp/lan-kline?symbol=600519.SH&period=15m&event='+encodeURIComponent(bars.at(-1).timestamp),{visible:80,period:'15m',mas:[{period:5,enabled:true},{period:233,enabled:false},{period:999,enabled:true}],extra:'old-tool'});
 t.after(()=>s.close());await pause();
 assert.equal(s.updates.at(-1).m.buys.length,3);assert.match(s.$('.kw-buy-count').textContent,/历史冻结BUY 3/);
 assert.equal(s.$('[data-action="add-ma"]'),null);assert.equal(s.dom.window.document.querySelectorAll('[data-ma-toggle]').length,6);
 assert.equal(s.$('[data-ma-toggle="233"]').checked,false);assert.equal(s.$('[data-ma-toggle="5"]').checked,true);
 const stored=JSON.parse(s.dom.window.localStorage.getItem('lan.kline.preferences.v2'));
 assert.deepEqual(Object.keys(stored).sort(),['maVisible','period','visible']);assert.equal(stored.maVisible[999],undefined);
 s.$('[data-action="refresh"]').click();await pause();assert.equal(s.updates.at(-1).m.buys.length,3);
 s.$('[data-period="30m"]').click();await pause();assert.equal(s.updates.at(-1).m.buys.length,0);
});

test('six ordered legend values and trailing checkboxes default on; toggles perform no IO or candle reload',async(t)=>{
 const s=setup();t.after(()=>s.close());await pause();
 const items=[...s.dom.window.document.querySelectorAll('.kw-ma-item')];
 assert.deepEqual(items.map(x=>x.querySelector('.kw-ma-name').textContent),['MA5','MA10','MA20','MA30','MA55','MA233']);
 for(const item of items){
  assert.deepEqual([...item.children].map(x=>x.tagName),['SPAN','OUTPUT','INPUT']);
  assert.equal(item.querySelector('input').checked,true);assert.equal(item.querySelector('output').textContent,'11.00');
 }
 const requests=s.requests.length,updates=s.updates.length;
 for(const n of [5,20]){const input=s.$(`[data-ma-toggle="${n}"]`);input.checked=false;input.dispatchEvent(new s.dom.window.Event('change',{bubbles:true}));}
 assert.equal(s.requests.length,requests);assert.equal(s.updates.length,updates);
 assert.deepEqual(Object.values(s.visibility.at(-1)),[false,true,false,true,true,true]);
 const input=s.$('[data-ma-toggle="5"]');input.checked=true;input.dispatchEvent(new s.dom.window.Event('change',{bubbles:true}));
 assert.equal(s.visibility.at(-1)[5],true);assert.equal(s.visibility.at(-1)[20],false);
 assert.equal(s.$('[data-ma-value="20"]').textContent,'11.00');
 assert.equal(s.$('.kw-status').textContent,'');assert.equal(s.$('.kw-event-detail'),null);assert.equal(s.$('.kw-footer'),null);
});

test('legend uses hovered saved values, clears all values for a gap and restores last completed values',async(t)=>{
 const source=data(),s=setup(source);t.after(()=>s.close());await pause();
 const periods=[5,10,20,30,55,233],bar=source.series['15m'].at(-3);
 s.readout({...bar,averages:periods.map(n=>[n,n+.456])});
 assert.deepEqual(periods.map(n=>s.$(`[data-ma-value="${n}"]`).textContent),['5.46','10.46','20.46','30.46','55.46','233.46']);
 s.readout({...bar,complete:false,averages:periods.map(n=>[n,123.456])});
 assert.ok(periods.every(n=>s.$(`[data-ma-value="${n}"]`).textContent==='—'));
 assert.match(s.$('.kw-readout').textContent,/缺口/);
 s.$('[data-action="refresh"]').click();await pause();
 assert.ok(periods.every(n=>s.$(`[data-ma-value="${n}"]`).textContent==='11.00'));
 s.reader.read=async()=>({...source,asset_type:'etf'});s.$('[data-action="refresh"]').click();await pause();
 s.readout({...bar,averages:periods.map(n=>[n,1.23456])});
 assert.ok(periods.every(n=>s.$(`[data-ma-value="${n}"]`).textContent==='1.235'));
});

test('controlled preferences survive period, refresh, stock changes and deep-link remount',async(t)=>{
 const source=data(),s=setup(source);t.after(()=>s.close());await pause();
 const input=s.$('[data-ma-toggle="10"]');input.checked=false;input.dispatchEvent(new s.dom.window.Event('change',{bubbles:true}));
 s.$('[data-period="60m"]').click();await pause();s.$('[data-action="refresh"]').click();await pause();
 assert.equal(s.$('[data-ma-toggle="10"]').checked,false);
 s.api.instrumentSearch=async()=>({results:[{symbol:'000001.SZ',name:'另一只测试股',asset_type:'stock'}]});
 s.reader.read=async symbol=>({...source,symbol,name:'另一只测试股'});
 s.$('[aria-label="股票或ETF搜索"]').value='000001.SZ';s.$('form').dispatchEvent(new s.dom.window.Event('submit',{bubbles:true,cancelable:true}));await pause();
 assert.equal(s.$('.kw-code').textContent,'000001.SZ');assert.equal(s.$('[data-ma-toggle="10"]').checked,false);
 const prefs=s.dom.window.localStorage.getItem('lan.kline.preferences.v2');
 const second=setup(source,'http://tsp/lan-kline?symbol=600519.SH&event='+encodeURIComponent(source.series['15m'].at(-1).timestamp),prefs,'lan.kline.preferences.v2');
 t.after(()=>second.close());await pause();
 assert.equal(second.requests[0].period,'15m');assert.equal(second.$('[data-ma-toggle="10"]').checked,false);
 assert.equal(second.$('[data-ma-toggle="5"]').checked,true);
});

test('malformed or unsupported local settings fall back to legal values without reviving old tools',async(t)=>{
 for(const prefs of ['broken','null','[]',JSON.stringify({period:'1m',visible:3,maVisible:{5:'false',10:0,20:false},font:'neon',mas:[{period:777,enabled:true}]})]){
  const s=setup(data(),'http://tsp/lan-kline?symbol=600519.SH',prefs,'lan.kline.preferences.v2');t.after(()=>s.close());await pause();
  const stored=JSON.parse(s.dom.window.localStorage.getItem('lan.kline.preferences.v2'));
  assert.equal(stored.period,'15m');assert.equal(stored.visible,120);
  assert.deepEqual(Object.keys(stored).sort(),['maVisible','period','visible']);
  assert.equal(stored.maVisible[5],true);assert.equal(stored.maVisible[10],true);
  assert.equal(s.dom.window.document.querySelectorAll('[data-ma-toggle]').length,6);
  assert.equal(s.$('[data-action="add-ma"]'),null);
 }
});

test('window-external events remain facts and only a compact time hint appears on the chart',async(t)=>{
 const source=data();source.events={rows:[],total:0,outside_window:7,truncated:false};
 const s=setup(source,'http://tsp/lan-kline?symbol=600519.SH&period=15m&event=2026-01-01T10:00:00&event_id=saved&rule_version=R1&defense=9&risk=private-json');
 t.after(()=>s.close());await pause();
 assert.match(s.$('.kw-anchor').textContent,/2026-01-01 10:00.*历史确认超出滚动窗口/);
 assert.equal(s.$('[data-action="focus"]').disabled,true);assert.match(s.$('.kw-buy-count').textContent,/7条窗口外/);
 assert.doesNotMatch(s.dom.window.document.body.textContent,/private-json|event_id|防守/);
 assert.deepEqual(s.updates.at(-1).m.buys,[]);
});

test('a 15m BUY between higher-period closes is a location hint, not an unverified-factor warning',async(t)=>{
 const source=data(),at=source.series['15m'].at(-4).timestamp;
 const s=setup(source,'http://tsp/lan-kline?symbol=600519.SH&period=60m&event='+encodeURIComponent(at)+'&factor=1');
 t.after(()=>s.close());await pause();s.$('[data-period="60m"]').click();await pause();
 assert.match(s.$('.kw-anchor').textContent,/目标时点不对应/);
 assert.doesNotMatch(s.$('[role="alert"]').textContent,/选中BUY复权基准未核实/);
});
