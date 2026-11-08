import { stamp } from '../src/aggregate.mjs';
export const A = '600000.SH', B = '159001.SZ';
export function session(day = '2026-11-06', base = 100) {
  const minutes = [...Array.from({ length: 120 }, (_, i) => 571 + i), ...Array.from({ length: 120 }, (_, i) => 781 + i)];
  const row = (minute, i) => ({ datetime: stamp(day, minute).replace(' ', 'T'), open: base + i, high: base + i + 2,
    low: base + i - 1, close: base + i + 1, volume: 10, amount: 1000 });
  return { date: day, rows: [row(570, 0), ...minutes.map(row)] };
}
export function apiFixture() {
  const calls = [], names = [{ symbol: A, name: '合成股票', asset_type: 'stock' }, { symbol: B, name: '合成ETF', asset_type: 'etf' }];
  const api = {
    async instrumentSearch(q, limit, assetTypes) { calls.push(['search', q, limit, assetTypes]); return { results: names.filter(n => n.symbol === q || n.name.includes(q)) }; },
    async klineMinuteRange(symbol, days) { calls.push(['range', symbol, days]); return { symbol, ...names.find(n => n.symbol === symbol), source: 'local', sessions: [session('2026-11-06', symbol === B ? 200 : 100)] }; },
    async klineDaily(symbol, days) { calls.push(['daily', symbol, days]); return { symbol, asset_type: symbol === B ? 'etf' : 'stock', rows: [{ date: '2026-11-06', open: 100, high: 103, low: 99, close: 102, volume: 1000 }] }; },
    async klineMinute() { throw Error('Unexpected provider request'); },
  };
  return { api, calls, names };
}
