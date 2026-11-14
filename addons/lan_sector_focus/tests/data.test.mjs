import test from 'node:test';
import assert from 'node:assert/strict';
import {loadInputs,verifyMembers} from '../src/data.mjs';
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
  assert.deepEqual(input.rotation.dates,[]);
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
  assert.equal(input.summary,null);assert.equal(input.day.total,0);
  assert.ok(!ctx.calls.some(c=>String(c[1]).includes('lan_ma55_day')));
});

test('installed managed Kline package is ready even when its metadata path is empty',async()=>{
  const ctx=sdk();ctx.api.extPagesList=async()=>[
    {enabled:false,path:'/lan-kline'},
    {enabled:true,path:'',managed_by:'lan.kline',version:'3.3.0',error:''},
  ];
  assert.equal((await loadInputs(ctx.api,now)).workbench,true);
});
