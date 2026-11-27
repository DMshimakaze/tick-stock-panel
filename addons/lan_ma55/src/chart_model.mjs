import { normalizedTime } from './workbench_data.mjs';

const china = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
  day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
export const beijing = seconds => china.format(new Date(seconds * 1000));
export const epoch = stamp => Date.parse(normalizedTime(stamp).replace(' ', 'T') + '+08:00') / 1000;

export const AVERAGE_COLORS = {
  5: '#F59E0B', 10: '#8B5CF6', 20: '#3B82F6', 30: '#EC4899', 55: '#F97316', 233: '#14B8A6',
};

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
  const averages = Object.fromEntries(Object.keys(AVERAGE_COLORS).map(period => {
    const key = `ma${period}`;
    const values = bars.map(bar => Number.isFinite(bar[key]) && bar.complete && bar.volume > 0
      ? { time: bar.time, value: bar[key] } : { time: bar.time });
    // Lightweight Charts joins across whitespace unless the outgoing line is transparent.
    for (let i = 0; i < values.length - 1; i++) {
      if (values[i].value !== undefined && values[i + 1].value === undefined) values[i].color = 'transparent';
    }
    return [period, values];
  }));
  const eventTimes = (payload.events || []).map(event => normalizedTime(event.at || event.timestamp));
  const confirmed = new Set(bars.filter(bar => bar.complete).map(bar => bar.timestamp));
  const anchor = selection.anchor ? bars.findIndex(bar => bar.complete && bar.timestamp === selection.anchor) : -1;
  return { bars, averages, anchor, times: eventTimes.filter(at => confirmed.has(at)).map(epoch),
    period: selection.timeframe, tick: payload.tick, count: rows.length };
}
