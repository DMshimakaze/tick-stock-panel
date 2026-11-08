import test from 'node:test';
import assert from 'node:assert/strict';
import { createReader,decodePayload,DEMAND,VIEW } from '../src/data.mjs';
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
test('one real factor projection affects only OHLC, missing coverage explicitly stays RAW',()=>{
  const raw={'5m':[['2026-11-06 09:35:00',10,12,9,11,3,3300]]};
  const q=decodePayload({symbol:'600519.SH',raw,ratios:{'2026-11-06':.5}},{}).sessions[0].rows[0];
  assert.equal(q.close,5.5);assert.equal(q.volume,3);assert.equal(q.amount,3300);
  assert.equal(decodePayload({raw,ratios:null},{}).sessions[0].rows[0].close,11);
  assert.throws(()=>decodePayload({raw,ratios:{}},{}),/投影缺少/);
});
