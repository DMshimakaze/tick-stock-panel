import { aggregateSessions, wallTime } from './aggregate.mjs';

export function assetTick(assetType) {
  // Asset type is resolved by TSP, never guessed from a six-digit code.
  return assetType === 'stock' ? .01 : assetType === 'etf' ? .001 : null;
}
export async function loadCatalog(api) {
  const [watchlist, groups, calendar] = await Promise.all([
    api.watchlistList(), api.watchlistGroups(), api.indexDaily('000001.SH', 120),
  ]);
  const names = watchlist.symbols.length ? await api.instrumentNames(watchlist.symbols.map(r => r.symbol)) : { names: {} };
  return { entries: watchlist.symbols.map(r => ({ ...r, name: r.name || names.names[r.symbol] || r.symbol })),
    groups: groups.groups, sessions: [...new Set(calendar.rows.map(r => r.date))].sort() };
}
export function adjustmentFactors(symbol, range, daily, asof) {
  const factors = {}, reasons = {}, dailyFactors = {};
  if (daily.symbol === symbol && daily.asset_type === range.asset_type && daily.source === 'enriched') {
    for (const row of daily.rows) {
      if ((!row.symbol || row.symbol === symbol) && Number.isFinite(row.close) && row.close > 0 &&
        Number.isFinite(row.raw_close) && row.raw_close > 0) dailyFactors[row.date] = row.close / row.raw_close;
    }
  }
  const rows = new Map(daily.rows.map(r => [r.date, r]));
  for (const session of range.sessions) {
    const row = rows.get(session.date), last = session.rows.find(r => wallTime(r.datetime).slice(11) === '15:00:00');
    let reason = '';
    if (daily.symbol !== symbol || range.symbol !== symbol || daily.asset_type !== range.asset_type ||
      (row?.symbol && row.symbol !== symbol)) reason = 'ASSET_BASIS_MISMATCH';
    else if (daily.source !== 'enriched' || !row || !Number.isFinite(row.raw_close) || row.raw_close <= 0 ||
      !Number.isFinite(row.close) || row.close <= 0) reason = 'HISTORICAL_FACTOR_UNAVAILABLE';
    else if (session.date < asof.slice(0, 10) && (!last || !Number.isFinite(last.close) ||
      Math.abs(last.close / row.close - 1) > 1e-6)) reason = 'MINUTE_DAILY_BASIS_UNVERIFIED';
    if (reason) reasons[session.date] = reason;
    else factors[session.date] = row.close / row.raw_close;
  }
  return { factors, reasons, dailyFactors };
}
function tailRun(bars) {
  let count = 0;
  for (let i = bars.length - 1; i >= 0 && bars[i].valid; i--) count++;
  return count;
}
export async function loadInstrument(api, symbol, asof) {
  let range = await api.klineMinuteRange(symbol, 10);
  let aggregated = aggregateSessions(range.sessions, asof);
  if (tailRun(aggregated.fifteen) < 56 && range.sessions.length >= 10) {
    range = await api.klineMinuteRange(symbol, 20);
    aggregated = aggregateSessions(range.sessions, asof);
  }
  const daily = range.sessions.length ? await api.klineDaily(symbol, 120) :
    { symbol, asset_type: range.asset_type, source: 'none', rows: [] };
  const basis = adjustmentFactors(symbol, range, daily, asof);
  return { symbol, name: range.name || daily.name || null, asset_type: range.asset_type,
    tick: assetTick(range.asset_type), ...basis, aggregated,
    today: range.sessions.find(s => s.date === asof.slice(0, 10))?.rows || [],
    daily, source: range.source, days: range.sessions.length };
}
export function mergeMinuteResponse(symbol, held, response, day) {
  if (response.truncated?.includes(symbol)) return { rows: held, blocked: 'MINUTE_TRUNCATED' };
  const incoming = response.data[symbol];
  if (!incoming) return response.incremental ? { rows: held, blocked: '' } : { rows: held, blocked: 'MINUTE_UNAVAILABLE' };
  const merged = new Map((response.incremental ? held : []).map(r => [wallTime(r.datetime), r]));
  for (const row of incoming) {
    const at = wallTime(row.datetime);
    if (at.slice(0, 10) !== day) return { rows: held, blocked: 'WRONG_TRADING_DATE' };
    merged.set(at, row); // The last dynamic 1m is deliberately replaceable.
  }
  return { rows: [...merged.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, r]) => r), blocked: '' };
}
export async function refreshInstruments(api, items, asof) {
  const day = asof.slice(0, 10);
  const lasts = items.map(item => item.today.at(-1)?.datetime).filter(Boolean).map(wallTime).sort();
  const response = await api.klineMinuteBatch(items.map(i => i.symbol), day, true,
    lasts.length === items.length ? lasts[0] : undefined);
  return Promise.all(items.map(async item => {
    try {
    const merged = mergeMinuteResponse(item.symbol, item.today, response, day);
    if (merged.blocked) return { ...item, blocked: merged.blocked };
    const daily = await api.klineDaily(item.symbol, 120);
    const range = { symbol: item.symbol, asset_type: item.asset_type, sessions: [{ date: day, rows: merged.rows }] };
    return { ...item, today: merged.rows, daily, ...adjustmentFactors(item.symbol, range, daily, asof),
      aggregated: aggregateSessions(range.sessions, asof), blocked: '',
      minute_status: response.full_minute_local ? '本地分钟增量' : '宿主分钟接口' };
    } catch (error) { return { ...item, blocked: String(error.message || error) }; }
  }));
}
