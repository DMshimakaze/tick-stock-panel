import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {mountIndustry} from '../src/page.mjs';
import {fixture,sdk,field,symbol,now} from './fixtures.mjs';

async function ready(root) {
  for(let i=0;i<50;i++) {
    await new Promise(resolve=>setImmediate(resolve));
    if(!root.querySelector('[data-action="refresh"]')?.disabled)return;
  }
  throw Error('page load did not finish');
}
async function mounted(f=fixture()) {
  const dom=new JSDOM('<main></main>',{url:'http://localhost/lan-industry'});
  const root=dom.window.document.querySelector('main'),ctx=sdk(f);
  const stop=mountIndustry(root,ctx,{now:()=>now});await ready(root);
  return {root,ctx,dom,stop};
}

test('industry-first UI exposes real dates, native deep link and no fictitious hierarchy',async()=>{
  const {root,ctx,stop}=await mounted();
  assert.match(root.textContent,/同花顺行业（扁平）/);assert.doesNotMatch(root.textContent,/二级行业|2级行业/);
  assert.match(root.textContent,/2026-10-19/);assert.match(root.textContent,/20 \/ 20/);
  const link=root.querySelector('[data-candidate]');assert.ok(link);
  const url=new URL(link.href);assert.equal(url.searchParams.get('source'),'TICKFLOW_NATIVE_15M');
  assert.equal(url.searchParams.get('period'),'15m');assert.equal(link.dataset.candidate,'native:test:1');
  const before=ctx.calls.length;root.querySelector('[data-action="industry-next"]').click();
  assert.equal(ctx.calls.length,before,'pagination is a snapshot cache hit');
  stop();assert.equal(root.childElementCount,0);
});

test('one stock with independent events renders once; member pagination is local',async()=>{
  const f=fixture();
  for(let n=30;n<47;n++)f.membership.rows.push({symbol:symbol(n),name:'成员'+n,[field]:'行业00'});
  f.membership.total=f.membership.rows.length;
  f.day.rows[0].events_json=JSON.stringify([f.event,{...f.event,event_id:'native:second',confirmed_at:'2026-11-13 14:45:00'}]);
  const {root,ctx,stop}=await mounted(f);
  assert.equal(root.querySelectorAll('.sf-stock').length,1);assert.equal(root.querySelectorAll('[data-candidate]').length,2);
  root.querySelector('[data-member-mode="all"]').click();assert.equal(root.querySelectorAll('.sf-stock').length,15);
  const before=ctx.calls.length;root.querySelector('[data-action="member-next"]').click();
  assert.equal(root.querySelectorAll('.sf-stock').length,3);assert.equal(ctx.calls.length,before);stop();
});

test('ambiguous labels and only-MA55/no-industry remain visible as unmatched',async()=>{
  const f=fixture();f.membership={total:1,rows:[{symbol:symbol(1),[field]:'设备-器械'}]};
  const {root,stop}=await mounted(f);
  assert.match(root.textContent,/1条分类待核实/);assert.equal(root.querySelectorAll('[data-sector]').length,0);
  root.querySelector('[data-action="unmatched"]').click();assert.equal(root.querySelectorAll('.sf-stock').length,1);
  assert.match(root.querySelector('.sf-members').textContent,/无已核实行业归属/);stop();
});

test('industry without candidates shows members, with no fake event or Kline link',async()=>{
  const f=fixture();f.day={total:0,rows:[]};const {root,stop}=await mounted(f);
  assert.equal(root.querySelectorAll('[data-candidate]').length,0);
  assert.match(root.querySelector('.sf-members').textContent,/没有对应原生15m候选/);
  root.querySelector('[data-member-mode="all"]').click();assert.equal(root.querySelectorAll('.sf-stock').length,1);stop();
});

test('empty RPS refresh keeps explicitly stale previous snapshot instead of a new valid zero',async()=>{
  const {root,ctx,stop}=await mounted();const before=root.querySelector('[aria-label="行业列表"]').textContent;
  ctx.api.rpsRotation=async()=>({dates:[],columns:{}});root.querySelector('[data-action="refresh"]').click();await ready(root);
  assert.equal(root.querySelector('[aria-label="行业列表"]').textContent,before);
  assert.match(root.textContent,/保留上次快照/);assert.match(root.querySelector('[role="alert"]').textContent,/空矩阵/);stop();
});

test('missing source and source dates expose explicit unavailability',async()=>{
  const f=fixture();f.config.latest_sync_date=null;f.rotation.dates=f.rotation.dates.slice(0,4);
  const {root,stop}=await mounted(f);
  assert.match(root.textContent,/INSUFFICIENT_HISTORY/);assert.match(root.textContent,/STALE_MEMBERSHIP|分类快照滞后或时间未知/);
  assert.equal(root.querySelector('.sf-kpi strong').textContent,'0');
  assert.equal(root.querySelector('[data-sector]').lastElementChild.textContent,'—');stop();
});
