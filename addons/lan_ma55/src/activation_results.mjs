import { escapeHtml as esc, icon, price, quantity } from './presentation.mjs';
import { PERIOD_LABEL, TIMEFRAMES } from './workbench_data.mjs';

const INTRADAY = TIMEFRAMES.slice(0, 4);
export const STATE_LABEL = { ACTIVATED: '激活', INACTIVE: '未激活', PENDING: '待算', MISSING: '缺数据' };

export function activationAccess(view, runtime, { statusError, viewError, now = Date.now() } = {}) {
  const targets = runtime?.activation_targets || {};
  const failed = Boolean(statusError || viewError || runtime?.publisher_error);
  const linked = Boolean(!failed && view?.result_id && view.result_id === runtime?.result_id
    && view.focus.revision === runtime.focus_revision && view.focus.taxonomy?.run === runtime.taxonomy_run
    && view.summary.expected === runtime.scope_count && view.summary.market_day === runtime.market_day
    && runtime.worker_alive && Number.isFinite(runtime.status_at_ms) && now - runtime.status_at_ms <= 45_000);
  const usable = TIMEFRAMES.filter(tf => linked && Object.hasOwn(targets, tf) &&
    Object.hasOwn(view.activation_targets || {}, tf) && targets[tf] === view.activation_targets[tf]);
  return { targets, usable, failed, day: runtime?.market_day || view?.summary?.market_day };
}

export function frameActivation(row, timeframe, access) {
  return access.usable.includes(timeframe) ? row.frames[timeframe]?.activation : undefined;
}

export function matrixSignal(row, timeframe, access) {
  const frame = frameActivation(row, timeframe, access);
  return frame?.state === 'ACTIVATED' ? frame.events.at(-1) : null;
}

export function activationResults(rows, access) {
  return Object.fromEntries(TIMEFRAMES.map(tf => {
    const target = access.targets[tf];
    let ready = 0, pending = 0;
    const latest = [], today = [];
    for (const row of rows) {
      const data = frameActivation(row, tf, access);
      const calculated = data?.state === 'ACTIVATED' || data?.state === 'INACTIVE';
      ready += Number(calculated);
      pending += Number(!data || data.state === 'PENDING');
      const events = data?.events || [];
      if (events.length) today.push({ row, events });
      if (calculated && events.at(-1)?.at === target) latest.push({ row, events: [events.at(-1)] });
    }
    const byTime = (a, b) => b.events.at(-1).at.localeCompare(a.events.at(-1).at) || a.row.symbol.localeCompare(b.row.symbol);
    return [tf, { target, total: rows.length, ready, pending, latest: latest.sort(byTime),
      today: today.sort(byTime), count: ready ? latest.length : null }];
  }));
}

const when = time => time?.slice(5, 16) || '—';
const isIntraday = timeframe => INTRADAY.includes(timeframe);

export function resultsMarkup(frames, access, { timeframe, today, expanded, scope }) {
  const data = frames[timeframe];
  const list = today ? data.today : data.latest;
  const usable = access.usable.includes(timeframe);
  const coverage = data.total && usable && data.target ?
    `${data.pending ? '计算中' : '已算'} ${quantity(data.ready)}/${quantity(data.total)}` : '';
  const missing = data.total - data.ready - data.pending;
  const empty = access.failed ? '结果暂不可用' : !usable ? '等待有效结果' : !data.total ?
    '当前范围暂无股票' : !data.target ? '等待该周期完成 K 线' : !data.ready && !list.length ? '等待有效结果' :
      data.ready === data.total ? '暂无激活' : '已算范围内暂无激活';
  const meta = [scope, PERIOD_LABEL[timeframe], data.target ? when(data.target) : (access.day || ''),
    coverage, coverage && missing ? `缺数据 ${quantity(missing)}` : '',
    today && list.length ? `今日 ${quantity(list.length)} 只` : ''].filter(Boolean).join(' · ');
  const tabs = TIMEFRAMES.map(tf => {
    const item = frames[tf];
    const progress = item.target && item.total && access.usable.includes(tf) ? `${quantity(item.ready)}/${quantity(item.total)}` : '—';
    return `<button type="button" data-result-period="${tf}" aria-pressed="${tf === timeframe}">
      <span>${PERIOD_LABEL[tf]} <b>${quantity(item.count)}</b></span><small>${when(item.target)}</small><small>${progress}</small></button>`;
  }).join('');
  const toggle = isIntraday(timeframe) ? `<div class="mr-segments" role="group" aria-label="激活时间范围">
    <button type="button" data-result-window="latest" aria-pressed="${!today}">本轮</button>
    <button type="button" data-result-window="today" aria-pressed="${today}">今日</button></div>` : '';
  const canExpand = list.length > 2 || (today && list.some(item => item.events.length > 1));
  const expand = canExpand ? `<button type="button" data-act="expand-results" aria-expanded="${expanded}">
    ${expanded ? '收起' : '展开全部'}${icon('chevron')}</button>` : '';
  const entries = (expanded ? list : list.slice(0, 2)).map(({ row, events }) => {
    const event = events.at(-1);
    const detail = expanded && events.length > 1 ? `<div class="mr-activation-more">${events.slice(0, -1).map(earlier =>
      `<button type="button" data-signal="${esc(earlier.id)}">${when(earlier.at)}${earlier.mode === 'REPLAY' ? ' · 补算' : ''}</button>`
    ).join('')}</div>` : '';
    return `<div><button type="button" class="mr-activation-entry" data-signal="${esc(event.id)}">
      <span><b>${esc(row.name)}</b> <small>${esc(row.symbol)}</small></span>
      <span>${when(event.at)}${event.mode === 'REPLAY' ? ' · 补算' : event.mode === 'DELAYED' ? ' · 延迟' : ''}</span>
      ${Number.isFinite(event.price) ? `<span>${price(event.price, row.raw_tick)}</span>` : '<span></span>'}${icon('chevron')}</button>${detail}</div>`;
  }).join('');
  return { tabs, controls: `<span class="mr-meta">${esc(meta)}</span>${toggle}${expand}`,
    list: entries || `<span class="mr-meta">${esc(empty)}</span>`, entries: list };
}
