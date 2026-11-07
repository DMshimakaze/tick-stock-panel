import { beijingNow } from './aggregate.mjs';

// Only official metadata reads. This neither reads MA55's database nor scans K lines.
export async function loadSupplyStatus(api, previous = {}) {
  const reads = {
    minute: () => api.minuteRefreshStatus(),
    snapshot: async () => {
      const result = await api.marketSnapshot();
      return { as_of: result.as_of, symbols: new Set(result.rows
        .filter(r => r.symbol && Number.isFinite(r.close) && r.close > 0).map(r => r.symbol)).size };
    },
    stocks: async () => {
      const result = await api.dataStatus();
      return { symbols: result.instruments?.symbols_covered ?? null,
        as_of: result.instruments?.latest_as_of ?? null, pending: result.stats_pending,
        checked_at: result.checked_at };
    },
    backend: async () => (await api.extensionPackages()).find(p => p.id === 'lan.ma55.state') ?? null,
  };
  const keys = Object.keys(reads);
  const results = await Promise.allSettled(keys.map(key => Promise.resolve().then(reads[key])));
  const checkedAt = beijingNow();
  return Object.fromEntries(keys.map((key, i) => [key, results[i].status === 'fulfilled'
    ? { data: results[i].value, checkedAt, error: '' }
    : { ...previous[key], error: String(results[i].reason?.message || results[i].reason) }]));
}
