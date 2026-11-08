import { beijing } from './aggregate.mjs';
export const fullSymbol = text => /^\d{6}\.(SH|SZ|BJ)$/.test(text.trim().toUpperCase()) ? text.trim().toUpperCase() : null;
export function liveTailAllowed(data, now = beijing()) {
  const day = now.slice(0, 10), weekday = new Date(day + 'T12:00:00+08:00').getUTCDay(), time = now.slice(11, 16);
  return weekday >= 1 && weekday <= 5 && data.daily.some(r => r.date === day)
    && (time >= '09:31' && time <= '11:30' || time >= '13:01' && time <= '15:00');
}
export async function searchInstruments(api, query) {
  if (!query.trim()) throw Error('请输入名称或完整代码，例如600519.SH');
  const data = await api.instrumentSearch(query.trim().toUpperCase(), 20, 'stock,etf');
  return data.results.filter(r => ['stock', 'etf'].includes(r.asset_type) && fullSymbol(r.symbol));
}
export function createReader(api) {
  const cache = new Map(); let chain = Promise.resolve(), generation = 0;
  function read(symbol, { force = false, tail = false, now = beijing() } = {}) {
    const id = ++generation;
    const job = chain.catch(() => {}).then(async () => {
      if (id !== generation) return null;
      if (!force && !tail && cache.has(symbol)) return cache.get(symbol);
      // The host daily `days` is a calendar-day window, not a candle count.
      const [minute, daily] = await Promise.allSettled([api.klineMinuteRange(symbol, 20), api.klineDaily(symbol, 400)]);
      if (id !== generation) return null;
      const data = { symbol, sessions: [], daily: [], errors: {}, asset_type: 'stock', name: symbol, readAt: now };
      for (const [kind, response] of [['minute', minute], ['daily', daily]]) {
        if (response.status === 'rejected') { data.errors[kind] = response.reason.message || String(response.reason); continue; }
        const value = response.value;
        if (value.symbol !== symbol) throw Error('官方响应标的与请求不一致，未显示行情');
        data.asset_type = value.asset_type || data.asset_type; data.name = value.name || data.name;
        if (kind === 'minute') { data.sessions = value.sessions; data.source = value.source; }
        else data.daily = value.rows;
      }
      if (tail) {
        if (!liveTailAllowed(data, now)) throw Error('当前不是已确认交易日的连续竞价时段，未请求即时尾部');
        const latest = await api.klineMinute(symbol, now.slice(0, 10), true);
        if (id !== generation) return null;
        if (latest.symbol !== symbol || latest.date !== now.slice(0, 10)) throw Error('即时尾部日期或标的不一致');
        const old = data.sessions.find(s => s.date === latest.date);
        const rows = new Map((old?.rows || []).map(r => [r.datetime, r]));
        for (const r of latest.rows) rows.set(r.datetime, r); // Explicit latest single-day revision replaces its own prior minute.
        data.sessions = [...data.sessions.filter(s => s.date !== latest.date), { date: latest.date, rows: [...rows.values()] }].sort((a, b) => a.date.localeCompare(b.date));
        data.tailSource = latest.source;
      }
      if (!Object.keys(data.errors).length) {
        cache.delete(symbol); cache.set(symbol, data);
        if (cache.size > 3) cache.delete(cache.keys().next().value);
      }
      return data;
    });
    chain = job; return job;
  }
  return { read, dispose() { generation++; cache.clear(); } };
}
