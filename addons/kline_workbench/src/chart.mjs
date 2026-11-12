import { createChart, CandlestickSeries, HistogramSeries, createSeriesMarkers, CrosshairMode } from 'lightweight-charts';
import { beijing } from './aggregate.mjs';
import { UP, DOWN, watchTheme } from './theme.mjs';
import { BuyColumns } from './buy-columns.mjs';
export function createWorkbenchChart(el, onReadout) {
  const style = el.ownerDocument.defaultView.getComputedStyle(el);
  const chart = createChart(el, { autoSize: true, layout: { background: { color: 'transparent' }, textColor: style.color, attributionLogo: true, fontFamily:'JetBrains Mono, monospace',fontSize:11 },
    grid: { vertLines: { color: '#80808014' }, horzLines: { color: '#80808020' } }, crosshair: { mode: CrosshairMode.Normal },
    localization: { locale: 'zh-CN', timeFormatter: t => beijing(Number(t) * 1000) },
    timeScale: { timeVisible: true, secondsVisible: false, lockVisibleTimeRangeOnResize:true, tickMarkFormatter: t => beijing(Number(t) * 1000)?.slice(5, 16) },
    rightPriceScale:{borderVisible:false,minimumWidth:58,scaleMargins:{top:.08,bottom:.06}},
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
  });
  const stopTheme=watchTheme(el,t=>chart.applyOptions({layout:{textColor:t.text,panes:{separatorColor:t.border}},
    grid:{vertLines:{visible:false},horzLines:{color:t.grid}},timeScale:{borderColor:t.border},
    crosshair:{vertLine:{color:t.crosshair,labelBackgroundColor:t.label},horzLine:{color:t.crosshair,labelBackgroundColor:t.label}}}));
  const candles = chart.addSeries(CandlestickSeries, { upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN });
  const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 1);
  chart.panes()[0].setStretchFactor(4); chart.panes()[1].setStretchFactor(1);
  const markers = createSeriesMarkers(candles, []);
  const priceColumns = new BuyColumns(), volumeColumns = new BuyColumns();
  candles.attachPrimitive(priceColumns); volume.attachPrimitive(volumeColumns);
  let buys = [];
  let bars = [], averages = new Map(), currentAnchor = -1, shown = 120, defenseLine;
  const report = time => {
    const index = bars.findIndex(b => b.time === time), bar = index >= 0 ? bars[index] : bars.findLast(b => b.complete);
    if (bar) onReadout({ ...bar, events: buys.filter(e=>e.time===bar.time), averages: bar.complete ? [...averages].map(([n, points]) => [n, points.find(p => p.time === bar.time)?.value]) : [] });
  };
  chart.subscribeCrosshairMove(p => report(p.time));
  function range(count, center = null) {
    shown = count;
    if (!bars.length) return;
    if (!count) { chart.timeScale().fitContent(); return; }
    const size = Math.min(count, bars.length), right = center === null ? bars.length - 1 : Math.min(bars.length - 1, Math.max(size - 1, center + Math.floor(size / 2)));
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, right - size + 1), to: right + 1 });
  }
  return {
    update(model, reset = false) {
      bars = model.bars; averages = model.averages; currentAnchor = model.anchor.index;
      const priceFormat = { type: 'price', precision: model.asset === 'etf' ? 3 : 2, minMove: model.asset === 'etf' ? .001 : .01 };
      candles.applyOptions({ priceFormat });
      if (defenseLine) { candles.removePriceLine(defenseLine); defenseLine=null; }
      if (model.defense) defenseLine=candles.createPriceLine({price:model.defense,color:'#8B5CF6',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'MA55防守'});
      candles.setData(bars.map(b => b.complete ? { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close } : { time: b.time }));
      volume.setData(bars.map(b => b.complete ? { time: b.time, value: b.volume, color: (b.close >= b.open ? UP : DOWN)+'88' } : { time: b.time }));
      buys = model.buys || [];
      priceColumns.setTimes(buys.map(e=>e.time)); volumeColumns.setTimes(buys.map(e=>e.time));
      const grouped = new Map();
      for (const event of buys) grouped.set(event.time, (grouped.get(event.time)||0)+1);
      const signalMarkers = [...grouped].map(([time,count])=>({ time, position:'belowBar', color:'#ff7383', shape:'arrowUp', text: count>1 ? `BUY ×${count}` : 'BUY' }));
      const setups = new Set(buys.map(e=>e.setup_at));
      for (const bar of bars) if (bar.complete && setups.has(bar.timestamp)) signalMarkers.push({time:bar.time,position:'belowBar',color:'#efb96c',shape:'circle',text:'支撑测试'});
      markers.setMarkers(signalMarkers.sort((a,b)=>a.time-b.time));
      if (reset) range(model.visible, currentAnchor < 0 ? null : currentAnchor);
      report(bars.findLast(b => b.complete)?.time);
    },
    range, focus() { if (currentAnchor >= 0) range(shown || 80, currentAnchor); },
    dates(from,to) { chart.timeScale().setVisibleRange({from,to}); },
    dispose() { stopTheme(); candles.detachPrimitive(priceColumns); volume.detachPrimitive(volumeColumns); markers.detach(); chart.remove(); },
  };
}
