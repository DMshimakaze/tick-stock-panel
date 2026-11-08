export const DEFAULT_MAS = [5, 10, 20, 30, 55, 233].map(period => ({ period, enabled: period === 55 || period === 233 }));
export function movingAverages(bars, periods) {
  const values = new Map(periods.map(n => [n, []])), queue = [];
  for (const bar of bars) {
    if (!bar.complete || bar.breakBefore) queue.length = 0;
    if (bar.complete) queue.push(bar.close);
    if (queue.length > Math.max(...periods, 1)) queue.shift();
    for (const n of periods) values.get(n).push(queue.length >= n && bar.complete
      ? { time: bar.time, value: queue.slice(-n).reduce((a, b) => a + b, 0) / n } : { time: bar.time });
  }
  return values;
}
export function eventAnchor(bars, event, period) {
  if (!event) return { index: -1, message: '' };
  const target = event.replace('T', ' '), index = bars.findIndex(b => b.timestamp === target && b.complete);
  if (index >= 0) return { index, message: `确认时点 ${target} · 标记原始15m信号结束时间` };
  if (!bars.length || target < bars[0].timestamp || target > bars.at(-1).timestamp)
    return { index: -1, message: '目标时点超出当前可得分钟历史' };
  return { index: -1, message: period === '15m' ? '目标时点缺少完整K，未标记' : '目标时点不对应当前级别完整K，请切换15m定位' };
}
