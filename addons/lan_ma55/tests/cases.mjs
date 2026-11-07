import { END_MINUTES, endStamp } from '../src/aggregate.mjs';

export function testGrid(count = 400) {
  const days = [], timestamps = [];
  for (let t = Date.UTC(2026, 8, 1); timestamps.length < count; t += 86_400_000) {
    const date = new Date(t);
    if ([0, 6].includes(date.getUTCDay())) continue;
    const day = date.toISOString().slice(0, 10);
    days.push(day); timestamps.push(...END_MINUTES.map(m => endStamp(day, m)));
  }
  return { days, timestamps };
}
export function scenario(candles = [], warmup = 128) {
  const { days, timestamps } = testGrid(warmup + candles.length + 32);
  const bars = Array.from({ length: warmup }, (_, i) => ({ timestamp: timestamps[i],
    open: 100, high: 100.15, low: 99.85, close: 100, volume: 100_005, valid: true, source_count: 3 }));
  for (const [open, high, low, close, volume] of candles) bars.push({ timestamp: timestamps[bars.length],
    open, high, low, close, volume, valid: true, source_count: 3 });
  return { bars, sessions: days, factors: Object.fromEntries(days.map(d => [d, 1])), tick: .01 };
}
export const SCENARIOS = {
  single: [[100, 102.5, 99.6, 101.8, 600_000]],
  duplicate: [[100, 102.5, 99.6, 101.8, 600_000], [101.8, 101.9, 100.2, 100.3, 120_000], [100.3, 102.5, 100.1, 102, 300_000]],
  departure: [[100, 103, 99.8, 102.2, 600_000], [102.3, 103.2, 102.2, 103, 180_000],
    [103, 103.1, 102.4, 102.5, 110_000], [102.5, 102.6, 101.5, 101.7, 100_000],
    [101.7, 101.7, 100.15, 100.3, 90_000], [100.3, 100.6, 100.02, 100.4, 110_010]],
  deeper: [[100, 102, 99.5, 101, 600_000], [101, 101.2, 100.1, 100.4, 80_010],
    [100.4, 101.5, 100.25, 101, 300_000], [101, 101.1, 98.9, 99.9, 100_005], [99.9, 101.2, 99.75, 101, 400_005]],
  recovery: [[98.8, 99.8, 98.4, 98.7, 400_005], [98.7, 99.5, 98.55, 99, 200_010], [99, 101.9, 99, 101.2, 700_005]],
  fresh: [[100, 102, 99.5, 101, 600_000], [101, 101.2, 100, 100.4, 80_010],
    [100.4, 100.5, 99.6, 100, 90_000], [100, 100.1, 99.7, 99.9, 90_000],
    [99.9, 100.1, 99.8, 100, 90_000], [100, 102, 99.8, 101.5, 600_000]],
};

export function toMinuteSessions(bars) {
  const days = new Map();
  for (const b of bars) {
    const day = b.timestamp.slice(0, 10), end = Number(b.timestamp.slice(11, 13)) * 60 + Number(b.timestamp.slice(14, 16));
    const rows = days.get(day) || [];
    for (let n = 0; n < 15; n++) rows.push({ datetime: endStamp(day, end - 14 + n).replace(' ', 'T'),
      open: b.open, high: n === 0 ? b.high : Math.max(b.open, n === 14 ? b.close : b.open),
      low: n === 0 ? b.low : Math.min(b.open, n === 14 ? b.close : b.open),
      close: n === 14 ? b.close : b.open, volume: b.volume / 15, amount: b.volume / 15 * b.close * 100 });
    days.set(day, rows);
  }
  return [...days].map(([date, rows]) => ({ date, rows }));
}
