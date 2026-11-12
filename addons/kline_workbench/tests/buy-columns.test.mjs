import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { BuyColumns, visibleBuys } from '../src/buy-columns.mjs';

test('every persisted visible BUY has exact candle identity; larger periods add none', () => {
  const bars = [1,2,3].map(n=>({timestamp:`T${n}`,time:n,complete:n!==2}));
  const events = [1,2,3,9].map(n=>({payload:{event_id:`E${n}`,confirmed_at:`T${n}`,defense:8,rule_version:'old'},risk:{reason:'saved'}}));
  const result = visibleBuys(bars,events,'15m');
  assert.deepEqual(result.map(e=>e.event_id),['E1','E3']);
  assert.equal(result[0].defense,8); assert.equal(result[0].risk.reason,'saved');
  assert.deepEqual(visibleBuys(bars,events,'30m'),[]);
  assert.deepEqual(visibleBuys(bars,events,'60m'),[]);
});

test('public primitive follows pan, zoom, pixel ratio and pane height every draw', () => {
  const primitive = new BuyColumns();
  let spacing=10, offset=0, redraws=0;
  const chart={timeScale:()=>({options:()=>({barSpacing:spacing}),timeToCoordinate:t=>t*spacing+offset})};
  primitive.attached({chart,requestUpdate:()=>redraws++}); primitive.setTimes([1,3,3]);
  const rects=[],context={save(){},restore(){},fillRect(...r){rects.push(r);}};
  const draw=(ratio,height)=>primitive.paneViews()[0].renderer().draw({useBitmapCoordinateSpace:fn=>fn({context,horizontalPixelRatio:ratio,bitmapSize:{height}})});
  draw(1,200); assert.deepEqual(rects.splice(0),[[5,0,10,200],[25,0,10,200]]);
  spacing=20; offset=-10; draw(2,400);
  assert.deepEqual(rects,[[0,0,40,400],[80,0,40,400]]);
  assert.equal(context.fillStyle,'#ff7383'); assert.equal(context.globalAlpha,.08);
  assert.equal(redraws,1); assert.equal(primitive.paneViews()[0].zOrder(),'bottom');
  primitive.detached(); assert.equal(primitive.chart,null);
});

test('chart contains no moving-average line creation path', async () => {
  const source=await readFile(new URL('../src/chart.mjs',import.meta.url),'utf8');
  assert.doesNotMatch(source,/LineSeries|withLineBreaks|maColors/);
  assert.match(source,/attachPrimitive/);
});
