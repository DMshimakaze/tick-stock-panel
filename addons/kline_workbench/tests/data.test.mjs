import test from 'node:test';
import assert from 'node:assert/strict';
import { createReader,decodePayload,DEMAND,VIEW,savedSeries,savedAverages } from '../src/data.mjs';
export function bridge() {
  const tables={[DEMAND]:new Map(),[VIEW]:new Map()}, calls=[];
  const api={
    async extDataRows(table,options) { calls.push(['read',table,options]);return {rows:[...tables[table].values()].map(r=>({...r}))}; },
    async extDataIngest(table,{rows}) {calls.push(['write',table]);for(const r of rows) tables[table].set(r.scope,{...r});},
  };
  const publish=()=>{for(const d of tables[DEMAND].values()) tables[VIEW].set(d.scope,{...d,revision:'1',status_json:JSON.stringify({phase:'READY'}),payload_json:JSON.stringify({symbol:d.requested_symbol,raw:{},ratios:null,basis:'RAW'})});};
  return {api,tables,calls,publish};
}
test('two simultaneous tabs claim distinct bounded slots through the official API',async()=>{
  const b=bridge(), a=createReader(b.api,{clientId:'a',clock:()=>100000}), c=createReader(b.api,{clientId:'b',clock:()=>100000});
  await Promise.all([a.read('600519.SH'),c.read('000001.SZ')]);b.publish();
  assert.equal((await a.poll()).symbol,'600519.SH');assert.equal((await c.poll()).symbol,'000001.SZ');
  assert.equal(b.tables[DEMAND].size,2);assert.ok(b.calls.every(r=>[DEMAND,VIEW].includes(r[1])));
  a.dispose();c.dispose();
});
test('rapid switch ignores a stale response and reuses its own slot',async()=>{
  const b=bridge(), r=createReader(b.api,{clientId:'a',clock:()=>100000});
  await r.read('600519.SH');b.publish();
  await r.read('000001.SZ');assert.equal((await r.poll()).pending,true);
  b.publish();assert.equal((await r.poll()).symbol,'000001.SZ');assert.equal(b.tables[DEMAND].size,1);
  assert.equal(await r.poll(),null);r.dispose();assert.equal(await r.poll(),null);
});
test('lease expiry permits reuse; 16 occupied live slots reports a real capacity limit',async()=>{
  const b=bridge();for(let i=0;i<16;i++) b.tables[DEMAND].set(String(i),{scope:String(i),client_id:'other'+i,request_ms:100000});
  const r=createReader(b.api,{clientId:'a',clock:()=>100000});await assert.rejects(()=>r.read('600519.SH'),/16个/);r.dispose();
  b.tables[DEMAND].get('0').request_ms=0;
  const s=createReader(b.api,{clientId:'b',clock:()=>100000});await s.read('600519.SH');assert.equal(b.tables[DEMAND].size,16);s.dispose();
});
test('server bars and MAs arrive unchanged, without a second price projection',()=>{
  const bar={timestamp:'2026-11-06 09:45:00',open:5,high:6,low:4.5,close:5.5,volume:300,ma55:5.3};
  const data=decodePayload({symbol:'600519.SH',series:{'15m':[bar]},basis:'QFQ'},{});
  assert.deepEqual(data.series['15m'][0],bar);assert.equal(data.series['1m'],undefined);
});

test('exchange neighbours cross lunch, overnight and holiday; real missing slots break the line',()=>{
  const calendar=['2026-09-30','2026-10-09'];
  const timestamps=['2026-09-30 11:30:00','2026-09-30 13:15:00','2026-09-30 15:00:00','2026-10-09 09:45:00'];
  const data={calendar,series:{'15m':timestamps.map((timestamp,i)=>({timestamp,complete:true,ma233:10+i}))}};
  const bars=savedSeries(data,'15m').bars;
  const ma=savedAverages(bars,[233]).get(233);
  assert.equal(ma[0].color,undefined); // lunch
  assert.equal(ma.find(p=>p.value===12).color,undefined); // closed days
  assert.equal(ma.find(p=>p.value===11).color,'transparent'); // actual missing 13:30
  assert.equal(ma.filter(p=>p.value!==undefined).length,4);
});
