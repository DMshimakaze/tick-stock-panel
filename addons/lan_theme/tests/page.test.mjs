import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {mountTheme} from '../src/page.mjs';
import {sdk} from './fixtures.mjs';
async function mounted(options) {
  const dom=new JSDOM('<main></main>',{url:'http://localhost/lan-theme'}),root=dom.window.document.querySelector('main'),ctx=sdk(options);
  const stop=mountTheme(root,ctx);
  for(let i=0;i<30&&root.textContent.includes('正在读取…');i++)await new Promise(r=>setTimeout(r,5));
  return {dom,root,ctx,stop};
}
test('three columns retain two-level paths, DEMO and historical classification warning',async()=>{
  const {root,ctx,stop}=await mounted();
  assert.match(root.textContent,/DEMO · 隔离测试关系/);assert.match(root.textContent,/按当前题材归属回看历史/);
  assert.equal(root.querySelectorAll('.lt-layout>.lt-column').length,3);
  assert.equal(root.querySelectorAll('tbody tr').length,3);
  const count=ctx.calls.length;
  root.querySelector('[data-theme="T1"]').click();root.querySelector('[data-branch="l2"]').click();
  assert.match(root.textContent,/供应商 \/ 整机/);assert.equal(root.querySelectorAll('tbody tr').length,1);
  root.querySelector('[data-reverse="300863.SZ"]').click();assert.match(root.querySelector('.lt-reverse').textContent,/供应商/);
  assert.equal(ctx.calls.length,count,'theme, branch and reverse filters make no requests');
  const url=new URL(root.querySelector('tbody a').href);assert.equal(url.searchParams.get('event'),'2026-11-06T10:15:00');
  stop();assert.equal(root.childElementCount,0);
});
test('WATCH is separate and Kline disabled retains official stock preview',async()=>{
  const {root,ctx,stop}=await mounted({enabled:false});
  assert.equal(root.querySelectorAll('tbody a').length,0);assert.match(root.textContent,/分钟K工作台未启用/);
  root.querySelector('[data-tab="WATCH"]').click();assert.equal(root.querySelectorAll('tbody tr').length,1);
  assert.match(root.querySelector('tbody').textContent,/WATCH/);assert.equal(root.querySelector('tbody tr').dataset.eventId,'');
  root.querySelector('[data-preview]').click();assert.equal(ctx.previews[0].symbol,'600519.SH');stop();
});
test('production empty source shows import requirement and zero fake intersections',async()=>{
  const {root,ctx,stop}=await mounted({empty:true});
  assert.match(root.textContent,/AUTHORIZED_SNAPSHOT_REQUIRED/);assert.equal(root.querySelectorAll('tbody tr').length,0);
  assert.doesNotMatch(root.textContent,/DEMO · 隔离测试关系/);
  assert.equal(ctx.calls.filter(c=>c[0]==='lan_theme_tree').length,0);stop();
});
test('failed refresh keeps prior complete rows and displays failure',async()=>{
  const {root,ctx,stop}=await mounted();const before=root.querySelector('tbody').textContent;
  ctx.api.extDataRows=async()=>{throw Error('publication unavailable');};root.querySelector('[data-action="refresh"]').click();
  await new Promise(r=>setTimeout(r,10));assert.equal(root.querySelector('tbody').textContent,before);
  assert.match(root.querySelector('[role="alert"]').textContent,/publication unavailable/);stop();
});
