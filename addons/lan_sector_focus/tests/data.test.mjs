import test from 'node:test';
import assert from 'node:assert/strict';
import {loadInputs,verifyMembers} from '../src/data.mjs';
import {project} from '../src/model.mjs';
import {fixture,sdk,field,now,symbol} from './fixtures.mjs';

test('SDK reads industry without any level and only the native MA55 projection',async()=>{
  const ctx=sdk(),input=await loadInputs(ctx.api,now);
  assert.deepEqual(ctx.calls.find(c=>c[0]==='rps'),['rps',21,'industry']);
  assert.ok(ctx.calls.findIndex(c=>c[0]==='market')<ctx.calls.findIndex(c=>c[0]==='rps'));
  assert.deepEqual(ctx.calls.filter(c=>c[0]==='rows').map(c=>c[1]),
    ['ext_hy_ths','lan_ma55_native15_summary_v1','lan_ma55_native15_day_v1']);
  assert.equal(input.workbench,true);assert.equal(input.errors.length,0);
});

test('industry errors retain independently readable native candidates; no retries or writes',async()=>{
  const ctx=sdk();let calls=0;ctx.api.rpsRotation=async()=>{calls++;throw Error('HTTP 403');};
  const input=await loadInputs(ctx.api,now);
  assert.equal(input.day.total,1);assert.equal(calls,1);assert.match(input.errors.join(' '),/HTTP 403/);
  assert.equal(input.rotation,null);assert.equal(input.sources.rotation.ok,false);
});

test('dimension-members fragment hits cannot introduce a membership or conceal truncation',async()=>{
  const ctx=sdk();ctx.api.dimensionMembers=async()=>({total:3,rows:[
    {symbol:symbol(1),[field]:'行业00'},
    {symbol:symbol(2),[field]:'行业00-子类'},
  ]});
  const result=await verifyMembers(ctx.api,'行业00',new Map([[symbol(1),{}]]));
  assert.equal(result.exact,1);assert.equal(result.excluded,1);assert.equal(result.complete,false);
});

test('missing native summary never causes an old BUY table read',async()=>{
  const f=fixture();f.summary=null;const ctx=sdk(f);
  const input=await loadInputs(ctx.api,now);
  assert.equal(input.summary,null);assert.equal(input.day,null);assert.equal(input.sources.day.ok,false);
  assert.ok(!ctx.calls.some(c=>String(c[1]).includes('lan_ma55_day')));
});

test('installed managed Kline package is ready even when its metadata path is empty',async()=>{
  const ctx=sdk();ctx.api.extPagesList=async()=>[
    {enabled:false,path:'/lan-kline'},
    {enabled:true,path:'',managed_by:'lan.kline',version:'3.3.0',error:''},
  ];
  assert.equal((await loadInputs(ctx.api,now)).workbench,true);
});

for(const status of [500,403])test(`day HTTP ${status} is unavailable, never a complete zero`,async()=>{
  const ctx=sdk(),original=ctx.api.extDataRows;
  ctx.api.extDataRows=async(id,opts)=>{
    if(id==='lan_ma55_native15_day_v1')throw Object.assign(Error(`HTTP ${status}`),{status});
    return original(id,opts);
  };
  const input=await loadInputs(ctx.api,now),v=project(input);
  assert.equal(v.candidates.complete,false);assert.equal(v.candidates.state,'SOURCE_UNAVAILABLE');
  assert.equal(v.candidates.eventCount,null);assert.equal(input.day,null);
  assert.equal(input.sources.day.ok,false);assert.equal(input.sources.day.total,null);
  assert.equal(input.sources.day.received,null);assert.equal(input.sources.day.http_status,status);
  assert.equal(v.scores.rows.filter(r=>r.valid).length,24,'industry source remains independently usable');
});

test('every source reports read outcome and real row dates; storage date is not candidate trading day',async()=>{
  const input=await loadInputs(sdk().api,now);
  for(const meta of Object.values(input.sources))for(const field of ['ok','error','read_at','as_of','total','received'])assert.ok(field in meta);
  assert.equal(input.sources.day.as_of,'2026-11-13');
  assert.equal(input.sources.day.storage_date,'2026-11-14 10:00:00');
  assert.equal(project(input).candidates.complete,true);
});

test('empty SDK body is an explicit read failure, not a successful empty candidate table',async()=>{
  const ctx=sdk(),original=ctx.api.extDataRows;
  for(const body of [null,{}]) {
    ctx.api.extDataRows=async(id,opts)=>id==='lan_ma55_native15_day_v1'?body:original(id,opts);
    const input=await loadInputs(ctx.api,now);
    assert.equal(input.sources.day.ok,false);assert.equal(project(input).candidates.state,'SOURCE_UNAVAILABLE');
  }
});
