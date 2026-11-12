import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { CandlestickSeries, HistogramSeries, LineSeries } from 'lightweight-charts';
import { createWorkbenchChart } from '../src/chart.mjs';
import { savedAverages } from '../src/data.mjs';
import { MA_COLORS } from '../src/theme.mjs';
import { epoch } from '../src/aggregate.mjs';

const periods=[5,10,20,30,55,233];
function fixture(t) {
  const dom=new JSDOM('<div/>',{pretendToBeVisual:true}), series=[],readouts=[];
  const chart={
    options:[],applyOptions(o){this.options.push(o);},
    addSeries(definition,options,pane=0){
      const api={definition,options:{...options},pane,primitives:[],data:[],
        applyOptions(o){Object.assign(this.options,o);},setData(d){this.data=d;},
        attachPrimitive(p){this.primitives.push(p);},detachPrimitive(p){this.primitives=this.primitives.filter(x=>x!==p);},
        createPriceLine(){throw Error('No fixed price decorations');}};
      series.push(api);return api;
    },
    panes:()=>[{setStretchFactor(){}},{setStretchFactor(){}}],
    subscribeCrosshairMove(fn){this.crosshair=fn;},
    timeScale:()=>({fitContent(){},setVisibleLogicalRange(){},setVisibleRange(){}}),
    remove(){this.removed=true;},
  };
  const plot=createWorkbenchChart(dom.window.document.querySelector('div'),b=>readouts.push(b),{makeChart:()=>chart});
  const bars=['09:45','10:00','10:15','10:30'].map((at,i)=>({
    timestamp:`2026-11-12 ${at}:00`,time:epoch(`2026-11-12 ${at}:00`),complete:i!==2,
    open:10,close:11,high:12,low:9,volume:300,
    ...Object.fromEntries(periods.map(n=>['ma'+n,n+i/10])),
  }));
  const model={bars,averages:savedAverages(bars,periods,'15m'),maVisible:Object.fromEntries(periods.map(n=>[n,true])),
    anchor:{index:0},asset:'stock',visible:120,period:'15m',buys:[{time:bars[0].time}],defense:9};
  t.after(()=>{plot.dispose();dom.window.close();});
  return {plot,chart,series,readouts,bars,model,dom};
}

test('six real LineSeries share candle scale, fixed colors and independent visibility',t=>{
  const s=fixture(t);s.plot.update(s.model,true);
  assert.equal(s.series.length,8);
  const candles=s.series.find(x=>x.definition===CandlestickSeries),volume=s.series.find(x=>x.definition===HistogramSeries);
  const lines=s.series.filter(x=>x.definition===LineSeries);
  assert.equal(lines.length,6);assert.deepEqual(lines.map(x=>x.options.color),MA_COLORS);
  for(const line of lines){
    assert.equal(line.pane,0);assert.equal(line.options.priceScaleId,'right');
    assert.equal(line.options.lineWidth,1);assert.equal(line.options.priceLineVisible,false);
    assert.equal(line.options.lastValueVisible,false);assert.equal(line.options.visible,true);
  }
  assert.equal(candles.options.priceLineVisible,false);assert.equal(candles.options.lastValueVisible,false);
  assert.equal(candles.primitives.length,1);assert.equal(volume.primitives.length,0);assert.equal(volume.pane,1);
  const candleData=candles.data;
  s.plot.setMaVisible({...s.model.maVisible,10:false,233:false});
  assert.deepEqual(lines.map(x=>x.options.visible),[true,false,true,true,true,false]);
  assert.equal(candles.data,candleData);
  s.plot.setMaVisible(s.model.maVisible);
  assert.ok(lines.every(x=>x.options.visible));assert.deepEqual(lines.map(x=>x.options.color),MA_COLORS);
});

test('legend readout follows actual completed candle, gaps and pointer leave',t=>{
  const s=fixture(t);s.plot.update(s.model,true);
  assert.deepEqual(s.readouts.at(-1).averages,periods.map(n=>[n,n+.3]));
  s.chart.crosshair({time:s.bars[0].time,point:{x:5,y:5}});
  assert.deepEqual(s.readouts.at(-1).averages,periods.map(n=>[n,n]));
  s.chart.crosshair({time:s.bars[2].time,point:{x:5,y:5}});
  assert.ok(s.readouts.at(-1).averages.every(([,v])=>v===undefined));
  s.chart.crosshair({point:{x:5,y:5}});
  assert.ok(s.readouts.at(-1).averages.every(([,v])=>v===undefined));
  s.chart.crosshair({});assert.equal(s.readouts.at(-1).timestamp,s.bars.at(-1).timestamp);
  s.plot.update({...s.model,asset:'etf'});
  assert.ok(s.series.filter(x=>x.definition!==HistogramSeries).every(x=>x.options.priceFormat.precision===3));
});

test('authoritative MA points cut missing and lunch/session connections without fabricated timestamps',()=>{
  const bars=['11:00','11:15','11:30','13:15','13:30','13:45','14:00'].map((at,i)=>({
    timestamp:`2026-11-12 ${at}:00`,time:epoch(`2026-11-12 ${at}:00`),complete:i!==4,ma5:i===4?null:20+i,ma233:null,
  }));
  const averages=savedAverages(bars,[5,233],'15m'),points=averages.get(5);
  assert.deepEqual(points.map(p=>p.time),bars.map(b=>b.time));
  assert.equal(points[2].color,'transparent');assert.equal(points[3].color,'transparent');
  assert.equal(points[4].value,undefined);assert.equal(points[5].value,25);
  assert.ok(averages.get(233).every(p=>p.value===undefined));
  assert.equal(points[0].color,undefined);
});

test('TSP theme changes update axes without changing MA colors or reloading candle data',async(t)=>{
 const s=fixture(t);s.plot.update(s.model,true);
 const candleData=s.series[0].data;
 s.dom.window.document.documentElement.classList.add('dark');await Promise.resolve();
 assert.equal(s.chart.options.at(-1).layout.textColor,'#A1A1AA');
 s.dom.window.document.documentElement.classList.remove('dark');await Promise.resolve();
 assert.equal(s.chart.options.at(-1).layout.textColor,'#71717A');
 assert.deepEqual(s.series.filter(x=>x.definition===LineSeries).map(x=>x.options.color),MA_COLORS);
 assert.equal(s.series[0].data,candleData);
});
