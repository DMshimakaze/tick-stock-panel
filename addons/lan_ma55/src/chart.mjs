// Price/volume panes, theme updates and event time columns from lan.kline 3.3.0.
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, CrosshairMode, LineStyle } from 'lightweight-charts';
import { AVERAGE_COLORS, beijing } from './chart_model.mjs';
import { BuyColumns } from './chart_columns.mjs';

export function createWorkbenchChart(el, onReadout, { makeChart = createChart } = {}) {
  const doc = el.ownerDocument, win = doc.defaultView;
  const chart = makeChart(el, {
    autoSize: true,
    layout: { background: { color: 'transparent' }, textColor: win.getComputedStyle(el).color,
      attributionLogo: false, fontFamily: 'inherit', fontSize: 11 },
    crosshair: { mode: CrosshairMode.Normal },
    localization: { locale: 'zh-CN', timeFormatter: t => beijing(t) },
    timeScale: { timeVisible: true, secondsVisible: false, lockVisibleTimeRangeOnResize: true,
      tickMarkFormatter: t => beijing(t).slice(5, 16) },
    rightPriceScale: { borderVisible: false, minimumWidth: 58, scaleMargins: { top: .08, bottom: .06 } },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
  });
  const candles = chart.addSeries(CandlestickSeries, { borderVisible: false, priceLineVisible: false, lastValueVisible: false });
  const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false,
    lastValueVisible: false }, 1);
  const averages = Object.fromEntries(Object.entries(AVERAGE_COLORS).map(([period, color]) =>
    [period, chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false })]));
  chart.panes()[0].setStretchFactor(4);
  chart.panes()[1].setStretchFactor(1);
  const columns = new BuyColumns();
  candles.attachPrimitive(columns);
  let model = null, hoverTime, priceLines = [], upperLevels = [];
  let bull, bear, accent;
  function theme() {
    const style = win.getComputedStyle(el), foreground = style.getPropertyValue('--fg-secondary').trim();
    const border = style.getPropertyValue('--border').trim(), primary = style.getPropertyValue('--fg-primary').trim();
    bull = style.getPropertyValue('--bull').trim() || '0 54% 52%';
    bear = style.getPropertyValue('--bear').trim() || '149 55% 40%';
    accent = style.getPropertyValue('--accent').trim() || '215 50% 52%';
    candles.applyOptions({ upColor: `hsl(${bull})`, downColor: `hsl(${bear})`,
      wickUpColor: `hsl(${bull})`, wickDownColor: `hsl(${bear})` });
    const textColor = foreground ? `hsl(${foreground})` : style.color;
    const gridColor = border ? `hsl(${border} / .65)` : '#aaaaaa38';
    const crosshairColor = primary ? `hsl(${primary} / .25)` : '#aaaaaa55';
    chart.applyOptions({ layout: { textColor, panes: { separatorColor: gridColor } },
      grid: { vertLines: { visible: false }, horzLines: { color: gridColor } },
      timeScale: { borderColor: gridColor }, crosshair: {
        vertLine: { color: crosshairColor }, horzLine: { color: crosshairColor },
      } });
    if (model) setVolume();
    setLevels(upperLevels);
  }
  function setLevels(levels) {
    upperLevels = levels;
    priceLines.forEach(line => candles.removePriceLine(line));
    priceLines = levels.map(({ price, label }) => candles.createPriceLine({
      price, title: label, lineWidth: 1, lineStyle: LineStyle.Dashed, color: `hsl(${accent} / .75)`,
      axisLabelVisible: true,
    }));
  }
  function setVolume() {
    volume.setData(model.bars.map(bar => bar.complete
      ? { time: bar.time, value: bar.volume, color: `hsl(${bar.close >= bar.open ? bull : bear} / .50)` }
      : { time: bar.time }));
  }
  function readout() {
    const bar = hoverTime == null ? model?.bars.findLast(row => row.complete)
      : model?.bars.find(row => row.time === hoverTime);
    onReadout(bar?.complete ? bar : null);
  }
  chart.subscribeCrosshairMove(point => { hoverTime = point.point ? point.time : undefined; readout(); });
  const observer = new win.MutationObserver(theme);
  observer.observe(doc.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
  theme();
  function focus(anchor) {
    if (!model?.bars.length) return;
    const size = Math.min(100, model.bars.length);
    const right = anchor < 0 ? model.bars.length - 1 : Math.min(model.bars.length - 1, Math.max(size - 1, anchor + Math.floor(size / 2)));
    chart.timeScale().setVisibleLogicalRange({ from: right - size + 1, to: right + 1 });
    if (anchor >= 0) {
      const bar = model.bars[anchor];
      chart.setCrosshairPosition(bar.close, bar.time, candles);
      hoverTime = bar.time;
      readout();
    }
  }
  return {
    update(next, reset) {
      model = next;
      const tick = next.tick > 0 ? next.tick : .01;
      const priceFormat = { type: 'price', precision: Math.max(0, Math.ceil(-Math.log10(tick))), minMove: tick };
      candles.applyOptions({ priceFormat });
      for (const series of Object.values(averages)) series.applyOptions({ priceFormat });
      chart.applyOptions({ timeScale: { timeVisible: next.period.endsWith('m'),
        tickMarkFormatter: t => next.period === 'weekly' ? beijing(t).slice(0, 7)
          : beijing(t).slice(5, next.period.endsWith('m') ? 16 : 10) } });
      candles.setData(next.bars.map(bar => bar.complete
        ? { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close } : { time: bar.time }));
      setVolume();
      for (const [period, series] of Object.entries(averages)) series.setData(next.averages[period]);
      columns.setTimes(next.times);
      if (reset) { hoverTime = undefined; focus(next.anchor); }
      readout();
    },
    focus() { if (model?.anchor >= 0) focus(model.anchor); },
    setAverageVisible(period, visible) { averages[period].applyOptions({ visible }); },
    setLevels,
    dispose() { observer.disconnect(); candles.detachPrimitive(columns); chart.remove(); },
  };
}
