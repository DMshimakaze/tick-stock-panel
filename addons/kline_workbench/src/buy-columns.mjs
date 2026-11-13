// Public Lightweight Charts series primitive: geometry is resolved every draw,
// so zoom, scrolling, DPR, pane resizing and theme changes share its time axis.
export class BuyColumns {
  constructor() {
    this.times = [];
    this.view = { zOrder: () => 'bottom', renderer: () => ({ draw: target => this.draw(target) }) };
  }
  attached({ chart, requestUpdate }) { this.chart = chart; this.requestUpdate = requestUpdate; }
  detached() { this.chart = null; this.requestUpdate = null; }
  paneViews() { return [this.view]; }
  setTimes(times) { this.times = [...new Set(times)]; this.requestUpdate?.(); }
  draw(target) {
    if (!this.chart) return;
    const scale = this.chart.timeScale(), spacing = scale.options().barSpacing;
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, bitmapSize }) => {
      context.save(); context.fillStyle = '#ff7383'; context.globalAlpha = .08;
      for (const time of this.times) {
        const x = scale.timeToCoordinate(time);
        if (x === null) continue;
        const left = Math.round((x - spacing / 2) * horizontalPixelRatio);
        const right = Math.round((x + spacing / 2) * horizontalPixelRatio);
        context.fillRect(left, 0, Math.max(1, right - left), bitmapSize.height);
      }
      context.restore();
    });
  }
}

export function visibleBuys(bars, events, period) {
  if (period !== '15m') return [];
  const complete = new Map(bars.filter(b => b.complete).map(b => [b.timestamp, b]));
  return events.map(row => ({ ...(row.payload || row), risk: row.risk || row.payload?.risk }))
    .filter(event => complete.has(event.confirmed_at))
    .map(event => ({ ...event, time: complete.get(event.confirmed_at).time }));
}
