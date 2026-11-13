// Synthetic fixtures only; production completed-bar validation is in Python.
const END_MINUTES=[585,600,615,630,645,660,675,690,795,810,825,840,855,870,885,900];
const endStamp=(day,minute)=>`${day} ${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}:00`;

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
