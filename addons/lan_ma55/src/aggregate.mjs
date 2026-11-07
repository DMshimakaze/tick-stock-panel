/** TSP's Beijing end-labelled 1m grid: 09:31..11:30 / 13:01..15:00.
 * 09:30 auction and 13:00 are never counted as regular-session minutes.
 */
export const END_MINUTES = [585, 600, 615, 630, 645, 660, 675, 690,
  795, 810, 825, 840, 855, 870, 885, 900];
export const FINALITY_MS = 60_000; // SDK has no final flag; don't consume its dynamic last minute.
export function wallTime(value) {
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}):(\d{2})(?:\.\d+)?(?:\+08:00)?$/.exec(String(value));
  if (!match) throw new Error('分钟时间必须是北京时间墙钟');
  return `${match[1]} ${match[2]}:${match[3]}`;
}
export function beijingNow(now = Date.now()) {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit',
    minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(new Date(now));
}
export function wallMillis(stamp) { return Date.parse(stamp.replace(' ', 'T') + '+08:00'); }
export function endStamp(day, minute) {
  return `${day} ${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}:00`;
}
export function isNextEnd(previous, current, sessions) {
  const oldDay = previous.slice(0, 10), day = current.slice(0, 10);
  const minute = s => Number(s.slice(11, 13)) * 60 + Number(s.slice(14, 16));
  const index = END_MINUTES.indexOf(minute(previous));
  if (index < 15) return oldDay === day && minute(current) === END_MINUTES[index + 1];
  return minute(current) === 585 && sessions.indexOf(day) === sessions.indexOf(oldDay) + 1 && sessions.includes(oldDay);
}
function validMinute(row) {
  return ['open', 'high', 'low', 'close'].every(k => Number.isFinite(row[k]) && row[k] > 0) &&
    Number.isFinite(row.volume) && row.volume >= 0 && row.low <= Math.min(row.open, row.close) &&
    row.high >= Math.max(row.open, row.close) && row.high >= row.low &&
    (row.amount === null || (Number.isFinite(row.amount) && row.amount >= 0));
}
function combine(rows, timestamp) {
  return { timestamp, open: rows[0].open, high: Math.max(...rows.map(r => r.high)),
    low: Math.min(...rows.map(r => r.low)), close: rows.at(-1).close,
    volume: rows.reduce((n, r) => n + r.volume, 0),
    amount: rows.every(r => Number.isFinite(r.amount)) ? rows.reduce((n, r) => n + r.amount, 0) : null,
    source_count: rows.length };
}
export function aggregateSessions(sessions, asof = beijingNow()) {
  const fifteen = [], five = [], issues = [];
  for (const session of [...sessions].sort((a, b) => a.date.localeCompare(b.date))) {
    const byTime = new Map(), conflicting = new Set();
    for (const row of session.rows) {
      const at = wallTime(row.datetime);
      if (at.slice(0, 10) !== session.date) throw new Error('分钟所属交易日不一致');
      const prior = byTime.get(at);
      if (prior && ['open', 'high', 'low', 'close', 'volume', 'amount'].some(k => prior[k] !== row[k])) conflicting.add(at);
      byTime.set(at, row);
    }
    for (const end of END_MINUTES) {
      const stamp = endStamp(session.date, end);
      if (wallMillis(stamp) + FINALITY_MS > wallMillis(asof)) continue;
      const source = Array.from({ length: 15 }, (_, i) => endStamp(session.date, end - 14 + i));
      const rows = source.map(t => byTime.get(t));
      const reason = source.some(t => conflicting.has(t)) ? 'CONFLICTING_MINUTE' :
        rows.some(r => !r) ? 'MISSING_MINUTE' : rows.some(r => !validMinute(r)) ? 'INVALID_OR_NULL_OPEN' : '';
      if (reason) {
        fifteen.push({ timestamp: stamp, valid: false, reason });
        issues.push({ timestamp: stamp, reason });
        continue;
      }
      const parts = [0, 5, 10].map(i => combine(rows.slice(i, i + 5), source[i + 4]));
      five.push(...parts);
      const bar = combine(parts, stamp), direct = combine(rows, stamp);
      const equal = ['open', 'high', 'low', 'close', 'volume', 'amount'].every(k =>
        bar[k] === direct[k] || (Number.isFinite(bar[k]) && Number.isFinite(direct[k]) &&
          Math.abs(bar[k] - direct[k]) <= 1e-8 + 1e-10 * Math.abs(direct[k])));
      fifteen.push({ ...bar, valid: equal && bar.volume > 0,
        reason: !equal ? 'AGGREGATE_MISMATCH' : bar.volume > 0 ? '' : 'NO_VOLUME' });
    }
  }
  return { fifteen, five, issues };
}
