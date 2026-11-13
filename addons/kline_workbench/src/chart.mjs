import { createChart, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode } from 'lightweight-charts';
import { beijing } from './aggregate.mjs';
import { UP, DOWN, MA_PERIODS, MA_COLORS, watchTheme } from './theme.mjs';
import { BuyColumns } from './buy-columns.mjs';

export function createWorkbenchChart(el, onReadout, {makeChart=createChart}={}) {
  const style=el.ownerDocument.defaultView.getComputedStyle(el);
  const chart=makeChart(el,{
    autoSize:true,
    layout:{background:{color:'transparent'},textColor:style.color,attributionLogo:false,fontFamily:'JetBrains Mono, monospace',fontSize:11},
    crosshair:{mode:CrosshairMode.Normal},
    localization:{locale:'zh-CN',timeFormatter:t=>beijing(Number(t)*1000)},
    timeScale:{timeVisible:true,secondsVisible:false,lockVisibleTimeRangeOnResize:true,tickMarkFormatter:t=>beijing(Number(t)*1000)?.slice(5,16)},
    rightPriceScale:{borderVisible:false,minimumWidth:58,scaleMargins:{top:.08,bottom:.06}},
    handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
    handleScale:{axisPressedMouseMove:true,mouseWheel:true,pinch:true},
  });
  const candles=chart.addSeries(CandlestickSeries,{
    upColor:UP,downColor:DOWN,borderVisible:false,wickUpColor:UP,wickDownColor:DOWN,
    priceScaleId:'right',priceLineVisible:false,lastValueVisible:false,
  });
  const volume=chart.addSeries(HistogramSeries,{
    priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:false,
  },1);
  const lines=new Map(MA_PERIODS.map((n,i)=>[n,chart.addSeries(LineSeries,{
    color:MA_COLORS[i],lineWidth:1,priceScaleId:'right',visible:true,
    priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,
  },0)]));
  chart.panes()[0].setStretchFactor(4);chart.panes()[1].setStretchFactor(1);
  const stopTheme=watchTheme(el,t=>chart.applyOptions({
    layout:{textColor:t.text,panes:{separatorColor:t.border}},
    grid:{vertLines:{visible:false},horzLines:{color:t.grid}},timeScale:{borderColor:t.border},
    crosshair:{vertLine:{color:t.crosshair,labelBackgroundColor:t.label},horzLine:{color:t.crosshair,labelBackgroundColor:t.label}},
  }));
  const priceColumns=new BuyColumns();candles.attachPrimitive(priceColumns);
  let bars=[],averages=new Map(),currentAnchor=-1,shown=120,hovering=false,hoverTime;
  function report() {
    const bar=(hovering?bars.find(b=>b.time===hoverTime):bars.findLast(b=>b.complete)) || {complete:false,timestamp:''};
    onReadout({...bar,averages:MA_PERIODS.map(n=>[n,bar.complete?averages.get(n)?.find(p=>p.time===bar.time)?.value:undefined])});
  }
  chart.subscribeCrosshairMove(p=>{hovering=Boolean(p.point);hoverTime=p.time;report();});
  function setMaVisible(visible) {
    for(const [n,line] of lines) line.applyOptions({visible:visible[n]!==false});
  }
  function range(count,center=null) {
    shown=count;
    if(!bars.length)return;
    if(!count){chart.timeScale().fitContent();return;}
    const size=Math.min(count,bars.length),right=center===null?bars.length-1:Math.min(bars.length-1,Math.max(size-1,center+Math.floor(size/2)));
    chart.timeScale().setVisibleLogicalRange({from:Math.max(0,right-size+1),to:right+1});
  }
  return {
    update(model,reset=false) {
      bars=model.bars;averages=model.averages;currentAnchor=model.anchor.index;
      const tick=model.rawTick>0?model.rawTick:model.asset==='etf'?.001:.01;
      const priceFormat={type:'price',precision:Math.max(0,Math.ceil(-Math.log10(tick))),minMove:tick};
      candles.applyOptions({priceFormat});
      candles.setData(bars.map(b=>b.complete?{time:b.time,open:b.open,high:b.high,low:b.low,close:b.close}:{time:b.time}));
      volume.setData(bars.map(b=>b.complete?{time:b.time,value:b.volume,color:(b.close>=b.open?UP:DOWN)+'88'}:{time:b.time}));
      for(const [n,line] of lines){line.applyOptions({priceFormat});line.setData(averages.get(n)||[]);}
      setMaVisible(model.maVisible);
      priceColumns.setTimes(model.period==='15m'?(model.buys||[]).map(e=>e.time):[]);
      if(reset){hovering=false;range(model.visible,currentAnchor<0?null:currentAnchor);}
      report();
    },
    setMaVisible,range,
    focus(){if(currentAnchor>=0)range(shown||80,currentAnchor);},
    dates(from,to){chart.timeScale().setVisibleRange({from,to});},
    dispose(){stopTheme();candles.detachPrimitive(priceColumns);chart.remove();},
  };
}
