import test from 'node:test';
import assert from 'node:assert/strict';
import { createReader, searchInstruments, fullSymbol, liveTailAllowed } from '../src/data.mjs';
import { A,B,apiFixture,session } from './fixtures.mjs';
const tick=()=>new Promise(r=>setTimeout(r,0));
test('official APIs only, bounded session cache, no automatic provider call',async()=>{
  const {api,calls}=apiFixture(), reader=createReader(api);
  await reader.read(A); await reader.read(A);
  assert.deepEqual(calls,[['range',A,20],['daily',A,400]]);
  reader.dispose();
});
test('one active symbol, skipped queued selections, stale results and disposal',async()=>{
  const {api,calls}=apiFixture(); let release;
  const original=api.klineMinuteRange;
  api.klineMinuteRange=(s,d)=>s===A?new Promise(r=>{release=()=>original(s,d).then(r);}):original(s,d);
  const reader=createReader(api), first=reader.read(A); await tick();
  const skipped=reader.read('600001.SH'), latest=reader.read(B);
  release(); assert.equal(await first,null); assert.equal(await skipped,null); assert.equal((await latest).symbol,B);
  assert.ok(!calls.some(c=>c[1]==='600001.SH'));
  const stopped=reader.read(A,{force:true});reader.dispose();assert.equal(await stopped,null);
});
test('typed search accepts stock/ETF including BJ, without guessed market or tick',async()=>{
  const {api}=apiFixture(); assert.equal(fullSymbol('600000'),null); assert.equal(fullSymbol(' 920001.bj '),'920001.BJ');
  assert.equal((await searchInstruments(api,B))[0].asset_type,'etf');
  await assert.rejects(()=>searchInstruments(api,''),/请输入/);
});
test('explicit live tail only for one confirmed trading symbol, never Sunday or lunch',async()=>{
  const {api}=apiFixture();let requests=0;
  api.klineDaily=async symbol=>({symbol,rows:[{date:'2026-11-09'}]});
  api.klineMinute=async symbol=>{requests++;return {symbol,date:'2026-11-09',source:'live',rows:session('2026-11-09').rows.slice(1,4)};};
  const reader=createReader(api);
  await assert.rejects(()=>reader.read(A,{tail:true,now:'2026-11-08 10:00:00'}),/不是已确认/);
  assert.equal(requests,0);
  const data=await reader.read(A,{tail:true,now:'2026-11-09 10:00:00'});
  assert.equal(requests,1); assert.equal(data.sessions.at(-1).date,'2026-11-09');
  assert.equal(liveTailAllowed(data,'2026-11-09 12:30:00'),false);
  reader.dispose();
});
