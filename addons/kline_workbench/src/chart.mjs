import { createChart, CandlestickSeries, HistogramSeries, LineSeries, createSeriesMarkers, CrosshairMode } from 'lightweight-charts';
import { beijing } from './aggregate.mjs';
const colors = ['#e6ba63', '#a995f4', '#5aace0', '#e789bb', '#f2a45b', '#64c7af'];
export function withLineBreaks(points, color) {
  // LWC joins non-whitespace line points. Its public per-point color controls
  // the outgoing segment: hide only the bridge across a missing MA run.
  return points.map((p,i) => p.value === undefined ? p : { ...p,
    color: i + 1 < points.length && points[i + 1].value === undefined ? 'transparent' : color });
}
export function createWorkbenchChart(el, onReadout) {
  const style = el.ownerDocument.defaultView.getComputedStyle(el);
  const chart = createChart(el, { autoSize: true, layout: { background: { color: 'transparent' }, textColor: style.color, attributionLogo: true },
    grid: { vertLines: { color: '#80808014' }, horzLines: { color: '#80808020' } }, crosshair: { mode: CrosshairMode.Normal },
    localization: { locale: 'zh-CN', timeFormatter: t => beijing(Number(t) * 1000) },
    timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: t => beijing(Number(t) * 1000)?.slice(5, 16) },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
  });
  const candles = chart.addSeries(CandlestickSeries, { upColor: '#ec6b75', downColor: '#42b79a', borderVisible: false, wickUpColor: '#ec6b75', wickDownColor: '#42b79a' });
  const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 1);
  chart.panes()[0].setStretchFactor(4); chart.panes()[1].setStretchFactor(1);
  const markers = createSeriesMarkers(candles, []), lines = new Map();
  let bars = [], averages = new Map(), currentAnchor = -1, shown = 120;
  const report = time => {
    const index = bars.findIndex(b => b.time === time), bar = index >= 0 ? bars[index] : bars.findLast(b => b.complete);
    if (bar) onReadout({ ...bar, averages: bar.complete ? [...averages].map(([n, points]) => [n, points.find(p => p.time === bar.time)?.value]) : [] });
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
      candles.setData(bars.map(b => b.complete ? { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close } : { time: b.time }));
      volume.setData(bars.map(b => b.complete ? { time: b.time, value: b.volume, color: b.close >= b.open ? '#ec6b7588' : '#42b79a88' } : { time: b.time }));
      for (const [n, series] of lines) if (!averages.has(n)) { chart.removeSeries(series); lines.delete(n); }
      let i = 0;
      for (const [n, points] of averages) {
        if (!lines.has(n)) lines.set(n, chart.addSeries(LineSeries, { title: `MA${n}`, color: colors[i % colors.length], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceFormat }));
        lines.get(n).applyOptions({ priceFormat, color: colors[i % colors.length] });
        lines.get(n).setData(withLineBreaks(points, colors[i % colors.length])); i++;
      }
      markers.setMarkers(currentAnchor < 0 ? [] : [{ time: bars[currentAnchor].time, position: 'belowBar', color: '#b89af5', shape: 'arrowUp', text: 'MA55 15m确认' }]);
      if (reset) range(model.visible, currentAnchor < 0 ? null : currentAnchor);
      report(bars.findLast(b => b.complete)?.time);
    },
    range, focus() { if (currentAnchor >= 0) range(shown || 80, currentAnchor); },
    dispose() { markers.detach(); chart.remove(); },
  };
}
