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

test('chart restores MA LineSeries while BUY coloring is attached only to price candles', async () => {
  const source=await readFile(new URL('../src/chart.mjs',import.meta.url),'utf8');
  assert.match(source,/chart\.addSeries\(LineSeries/);
  assert.match(source,/candles\.attachPrimitive\(priceColumns\)/);
  assert.doesNotMatch(source,/volume\.attachPrimitive|createSeriesMarkers|createPriceLine|MA55防守/);
});

test('off-axis columns skip missing coordinates and keep the same soft color after theme and size changes',()=>{
 const primitive=new BuyColumns(),rects=[];
 let spacing=8,offset=0;
 primitive.attached({chart:{timeScale:()=>({options:()=>({barSpacing:spacing}),timeToCoordinate:t=>t===2?null:t*spacing+offset})},requestUpdate(){}});
 primitive.setTimes([1,2,1]);
 for(const background of ['#09090b','#ffffff']){
  const context={background,save(){},restore(){},fillRect(...rect){rects.push(rect);}};
  primitive.draw({useBitmapCoordinateSpace:fn=>fn({context,horizontalPixelRatio:1.5,bitmapSize:{height:180}})});
  assert.equal(context.fillStyle,'#ff7383');assert.equal(context.globalAlpha,.08);
 }
 assert.deepEqual(rects,[[6,0,12,180],[6,0,12,180]]);
 spacing=12;offset=-6;rects.length=0;
 primitive.draw({useBitmapCoordinateSpace:fn=>fn({context:{save(){},restore(){},fillRect(...r){rects.push(r);}},horizontalPixelRatio:2,bitmapSize:{height:300}})});
 assert.deepEqual(rects,[[0,0,24,300]]);
});
