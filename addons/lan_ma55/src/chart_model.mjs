import { normalizedTime } from './workbench_data.mjs';

const china = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
  day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
export const beijing = seconds => china.format(new Date(seconds * 1000));
export const epoch = stamp => Date.parse(normalizedTime(stamp).replace(' ', 'T') + '+08:00') / 1000;

export function chartModel(payload, selection) {
  const rows = payload.bars || [];
  const byTime = new Map(rows.map(bar => [normalizedTime(bar.timestamp), bar]));
  const first = normalizedTime(rows[0]?.timestamp);
  const last = normalizedTime(rows.at(-1)?.timestamp);
  const grid = (payload.grid || [...byTime.keys()]).filter(at => first && at >= first && at <= last);
  const bars = grid.map(timestamp => {
    const bar = byTime.get(timestamp);
    return { ...bar, timestamp, time: epoch(timestamp), complete: bar?.complete === true };
  });
  const averages = bars.map(bar => Number.isFinite(bar.ma55) && bar.complete
    ? { time: bar.time, value: bar.ma55 } : { time: bar.time });
  // Lightweight Charts joins across whitespace unless the outgoing line is transparent.
  for (let i = 0; i < averages.length - 1; i++) {
    if (averages[i].value !== undefined && averages[i + 1].value === undefined) averages[i].color = 'transparent';
  }
  const eventTimes = (payload.events || []).map(event => normalizedTime(event.at || event.timestamp));
  const confirmed = new Set(bars.filter(bar => bar.complete).map(bar => bar.timestamp));
  const anchor = selection.anchor ? bars.findIndex(bar => bar.complete && bar.timestamp === selection.anchor) : -1;
  return { bars, averages, anchor, times: eventTimes.filter(at => confirmed.has(at)).map(epoch),
    period: selection.timeframe, tick: payload.tick, count: rows.length };
}
