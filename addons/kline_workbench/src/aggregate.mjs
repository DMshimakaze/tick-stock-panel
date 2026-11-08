// End-stamped Beijing exchange bars. A missing input stays whitespace.
export const PERIODS = ['15m', '30m', '60m'];
const fields = ['open', 'high', 'low', 'close', 'volume', 'amount'];
const cn = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
export function beijing(value = new Date()) {
  if (typeof value === 'string' && /^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d$/.test(value)) return value.replace('T', ' ');
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? cn.format(date) : null;
}
export const epoch = stamp => Date.parse(stamp.replace(' ', 'T') + '+08:00') / 1000;
export const stamp = (day, minute) => `${day} ${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}:00`;
export function valid(row) {
  return row && ['open', 'high', 'low', 'close'].every(k => Number.isFinite(row[k]) && row[k] > 0)
    && Number.isFinite(row.volume) && row.volume >= 0
    && row.low <= Math.min(row.open, row.close) && Math.max(row.open, row.close) <= row.high;
}
function result(bars, counts) {
  const complete = bars.filter(b => b.complete);
  let continuous = 0;
  for (const b of bars) continuous = b.complete ? (b.breakBefore ? 1 : continuous + 1) : 0;
  return { bars, ...counts, validCount: complete.length, continuous, gaps: bars.length - complete.length,
    dataAsOf: complete.at(-1)?.timestamp || null, dates: [...new Set(complete.map(b => b.timestamp.slice(0, 10)))] };
}
export function aggregateMinutes(sessions, period, now = beijing(), calendar = []) {
  const size = Number(period.replace('m', ''));
  if (![1, 5, 15, 30, 60].includes(size)) throw Error('不支持的分钟级别');
  const input = new Map(), counts = { received: 0, pending: 0, auction: 0, invalidTimes: 0, conflicts: 0 };
  const knownDays = sessions.map(s => s.date).sort();
  for (const session of sessions) for (const row of session.rows) {
    counts.received++;
    const at = beijing(row.datetime), match = at?.match(/^(\d{4}-\d\d-\d\d) (\d\d):(\d\d):00$/);
    if (!match || match[1] !== session.date) { counts.invalidTimes++; continue; }
    const minute = Number(match[2]) * 60 + Number(match[3]);
    if (minute === 570) { counts.auction++; continue; }
    if (!(minute >= 571 && minute <= 690 || minute >= 781 && minute <= 900)) { counts.invalidTimes++; continue; }
    if (input.has(at) && (input.get(at) === null || fields.some(k => (input.get(at)[k] ?? null) !== (row[k] ?? null)))) {
      input.set(at, null); counts.conflicts++;
    } else if (!input.has(at)) input.set(at, row);
  }
  const days = [...new Set([...knownDays, ...calendar.filter(d => d >= knownDays[0] && d <= knownDays.at(-1))])].sort();
  const cutoff = epoch(now) - 60, bars = [];
  for (const day of days) for (const start of [570, 780]) for (let end = start + size; end <= start + 120; end += size) {
    const timestamp = stamp(day, end), time = epoch(timestamp);
    if (time > cutoff) { counts.pending++; continue; }
    const required = Array.from({ length: size }, (_, i) => stamp(day, end - size + i + 1));
    const rows = required.map(t => input.get(t));
    const breakBefore = !calendar.length && end === 570 + size;
    if (!rows.every(valid)) {
      bars.push({ timestamp, time, complete: false, reason: '缺分钟、冲突或无效OHLCV', breakBefore }); continue;
    }
    bars.push({ timestamp, time, complete: true, breakBefore, open: rows[0].open, high: Math.max(...rows.map(r => r.high)),
      low: Math.min(...rows.map(r => r.low)), close: rows.at(-1).close, volume: rows.reduce((n, r) => n + r.volume, 0),
      amount: rows.every(r => Number.isFinite(r.amount)) ? rows.reduce((n, r) => n + r.amount, 0) : null });
  }
  return result(bars, counts);
}
export function aggregateFive(sessions, period, now = beijing(), calendar = []) {
  const size = Number(period.replace('m',''));
  if (![5,15,30,60].includes(size)) throw Error('5m原始K不能生成1m');
  const input = new Map(sessions.flatMap(s=>s.rows.map(r=>[r.datetime,r])));
  const known = sessions.map(s=>s.date).sort();
  const days = calendar.filter(d=>d>=known[0] && d<=known.at(-1));
  const counts = {received:input.size,pending:0,auction:0}, bars=[], cutoff=epoch(now)-60;
  for (const day of days.length ? days : known) for (const start of [570,780]) for (let end=start+size;end<=start+120;end+=size) {
    const timestamp=stamp(day,end), time=epoch(timestamp);
    if (time>cutoff) { counts.pending++; continue; }
    const rows=Array.from({length:size/5},(_,i)=>input.get(stamp(day,end-size+5+i*5)));
    const breakBefore=!calendar.length && end===570+size;
    if (!rows.every(valid)) { bars.push({timestamp,time,complete:false,breakBefore,reason:'缺少真实5m输入'}); continue; }
    bars.push({timestamp,time,complete:true,breakBefore,open:rows[0].open,high:Math.max(...rows.map(r=>r.high)),low:Math.min(...rows.map(r=>r.low)),
      close:rows.at(-1).close,volume:rows.reduce((s,r)=>s+r.volume,0),amount:rows.every(r=>Number.isFinite(r.amount))?rows.reduce((s,r)=>s+r.amount,0):null});
  }
  return result(bars,counts);
}
export function dailyCandles(rows, now = beijing(), calendar = []) {
  const counts = { received: rows.length, pending: 0, auction: 0 }, cutoff = epoch(now) - 60;
  const ordered = [...rows].sort((a,b)=>a.date.localeCompare(b.date)), byDay=new Map(rows.map(r=>[r.date,r]));
  const days = calendar.length ? calendar.filter(d=>d>=ordered[0]?.date && d<=ordered.at(-1)?.date) : ordered.map(r=>r.date);
  const bars = days.flatMap(date => {
    const row=byDay.get(date), timestamp = date + ' 15:00:00', time = epoch(timestamp);
    if (time > cutoff) { counts.pending++; return []; }
    return [{ ...row, time, timestamp, complete: Boolean(valid(row)), reason: '日K缺失或字段无效' }];
  });
  return result(bars, counts);
}
