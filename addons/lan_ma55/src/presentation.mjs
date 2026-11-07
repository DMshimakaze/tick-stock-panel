import { minimalFeatures } from './engine.mjs';
import { isNextEnd } from './aggregate.mjs';

export const REASONS = {
  WARMUP: '数据不足，等待56根连续完成K', INSUFFICIENT_WARMUP: '数据不足，等待56根连续完成K',
  MISSING_MINUTE: '缺少真实1分钟K', INVALID_OR_NULL_OPEN: '分钟OHLC缺失或无效',
  CONFLICTING_MINUTE: '同一分钟数据冲突', NO_VOLUME: '无有效成交量',
  MISSING_COMPLETED_15M: '完成K不连续，重新预热', PRICE_BASIS_CHANGED: '复权尺度变化，重新预热',
  HISTORICAL_FACTOR_UNAVAILABLE: '缺少同日原价/复权价，暂停确认',
  MINUTE_DAILY_BASIS_UNVERIFIED: '分钟与日K价格口径待确认', ASSET_BASIS_MISMATCH: '资产/价格口径不一致',
  TRADING_CALENDAR_UNAVAILABLE: '缺少官方交易日证据', TICK_UNAVAILABLE: '报价tick未确认',
  MINUTE_TRUNCATED: '宿主返回残缺分钟，暂停确认', MINUTE_UNAVAILABLE: '宿主暂无当日分钟',
  WRONG_TRADING_DATE: '返回分钟日期不一致', AWAIT_SUPPORT_TEST: '已有推动，等待支撑测试',
  NEW_LOWER_FOOT: '出现更深支撑脚，等待转强', CLOSE_BELOW_SUPPORT: '收盘跌破支撑',
  SAME_SUPPORT_SWING: '同一支撑波段已确认', NO_REAL_ADVANCE: '尚无有效推动',
  AWAIT_FIRST_RED: '等待第一根转强确认', BUY_STRUCTURE: '结构已确认',
};
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const number = (v, digits = 3) => Number.isFinite(v) ? v.toFixed(digits) : '—';
const pattern = value => value === 'SINGLE_BAR' ? '单K收回' : value === 'MULTI_BAR' ? '多K测试' : '—';
const mode = value => ({ LIVE: '实时确认', DELAYED: '延迟确认', REPLAY: '历史重放' }[value] || '');

export function chartBars(bars, factors, sessions) {
  let history = [], previous = null, lastFactor = null;
  return bars.map(bar => {
    const factor = factors[bar.timestamp.slice(0, 10)];
    const gap = previous && !isNextEnd(previous, bar.timestamp, sessions);
    const changed = factor && lastFactor && Math.abs(factor / lastFactor - 1) > 1e-6;
    previous = bar.timestamp; lastFactor = factor;
    if (!bar.valid || !factor || gap || changed) { history = []; return { ...bar, ma55: NaN, known_ma55: NaN }; }
    const b = minimalFeatures(history, bar, []);
    history = [...history, bar].slice(-55);
    return b;
  });
}
function chart(rows, event) {
  const eventIndex = event ? rows.findIndex(r => r.timestamp === event.confirmed_at) : -1;
  const end = eventIndex >= 0 ? Math.min(rows.length, eventIndex + 12) : rows.length;
  const bars = rows.slice(Math.max(0, end - 80), end), valid = bars.filter(r => r.valid);
  if (!valid.length) return '<div class="lm-empty">尚无完整15分钟K；请使用TSP官方分钟历史同步。</div>';
  const w = 920, h = 270, pad = 28, lows = valid.map(r => r.low), highs = valid.map(r => r.high);
  const ma = valid.map(r => r.ma55).filter(Number.isFinite);
  const lo = Math.min(...lows, ...ma), hi = Math.max(...highs, ...ma), span = hi - lo || hi * .01;
  const x = i => pad + (w - pad * 2) * (i + .5) / bars.length;
  const y = p => h - pad - (p - lo) / span * (h - pad * 2);
  let path = '', active = false;
  const candles = bars.map((b, i) => {
    if (!b.valid) { active = false; return ''; }
    if (Number.isFinite(b.ma55)) { path += `${active ? 'L' : 'M'}${x(i)},${y(b.ma55)} `; active = true; }
    else active = false;
    const color = b.close >= b.open ? '#ef6469' : '#36ab8e';
    const width = Math.max(2, (w - pad * 2) / bars.length * .55);
    const marker = (event?.confirmed_at === b.timestamp ? `<text x="${x(i)}" y="${Math.max(13, y(b.high) - 10)}" fill="#ef6469" text-anchor="middle">BUY</text>` : '') +
      (event?.setup_at === b.timestamp ? `<text x="${x(i)}" y="${Math.min(h - 5, y(b.low) + 18)}" text-anchor="middle" fill="#d68b30">脚</text>` : '') +
      (event?.impulse?.at === b.timestamp ? `<text x="${x(i) + 18}" y="${Math.max(13, y(b.high) - 10)}" text-anchor="middle" fill="#9c8eff">推</text>` : '');
    return `<g><title>${escape(b.timestamp)} · O ${number(b.open)} H ${number(b.high)} L ${number(b.low)} C ${number(b.close)} · 当时已知MA55 ${number(b.known_ma55)}</title><line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${color}"/><rect x="${x(i) - width / 2}" y="${Math.min(y(b.open), y(b.close))}" width="${width}" height="${Math.max(1, Math.abs(y(b.open) - y(b.close)))}" fill="${color}"/>${marker}</g>`;
  }).join('');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="已完成15分钟蜡烛图与MA55"><text x="4" y="16" fill="currentColor" font-size="11">${number(hi)}</text><text x="4" y="${h - 9}" fill="currentColor" font-size="11">${number(lo)}</text>${candles}<path d="${path}" fill="none" stroke="#a596fa" stroke-width="1.8"/></svg><div class="lm-meta">${escape(bars[0]?.timestamp)} → ${escape(bars.at(-1)?.timestamp)} · 紫线 MA55 · 悬停查看当时已知MA55${event && eventIndex < 0 ? ' · 此确认不在当前本地历史窗口内' : ''}</div>`;
}
function detail(item, event, uiAvailable) {
  if (!item) return '<div class="lm-empty">选择一行，查看结构和完成K证据。</div>';
  const s = item.record?.state, d = event?.decision || s?.decision;
  const risk = event ? item.record.risks[event.event_id] : null;
  const snapshots = item.record?.confirmations || [];
  return `<div class="lm-detail-head"><div><b>${escape(item.name)} <span class="lm-meta">${escape(item.symbol)}</span></b><div class="lm-meta">${event ? `${mode(event.mode)} · ${pattern(event.pattern)} · ${escape(event.confirmed_at)}` : escape(REASONS[item.blocked || s?.reason] || item.error || '暂无结构')}</div></div><button data-action="preview" data-symbol="${escape(item.symbol)}" ${uiAvailable ? '' : 'disabled'} title="${uiAvailable ? '打开TSP个股详情' : '当前宿主未提供个股详情能力'}">官方个股详情 ↗</button></div>
    ${!uiAvailable ? '<p class="lm-warning">当前宿主未提供个股详情入口。</p>' : ''}
    ${event?.risk_text ? `<p class="lm-warning">${escape(event.risk_text)}</p>` : ''}
    <div class="lm-facts">${[
      ['测试开始', event?.setup_at || d?.support_test_time || '—'], ['确认结束', event?.confirmed_at || '尚未确认'],
      ['确认数据时点', event?.data_asof || s?.data_asof || '—'], ['首次看到', event?.first_seen_at || '—'],
      ['当时已知 MA55', number(d?.known_ma55)], ['确认/当前 MA55', number(d?.ma55)],
      ['确认收盘', number(event?.confirm_close)], ['距离 MA55', `${number(d?.close_distance_pct, 2)}%`],
      ['前20根量比', number(d?.volume_ratio20, 2)], ['同钟点量比', number(d?.volume_ratio_same_clock, 2)],
      ['支撑防守点', number(event?.defense || d?.support_test_low)], ['推动K', event?.impulse?.at || s?.structure?.last_impulse?.at || '—'],
      ['调整后 tick', event ? number(event.raw_tick * event.adjust_factor, 6) : '逐日校验'],
      ['后续风险', risk?.invalidated_at ? `跌破防守 ${risk.invalidated_at}` : risk?.data_pending_at ? `数据待定 ${risk.data_pending_at}` : risk?.extended ? '离均线较远，不追价' : risk ? `已过 ${risk.age_bars} 根K` : '—'],
    ].map(([label, value]) => `<div><span>${label}</span><strong>${escape(value)}</strong></div>`).join('')}</div>
    ${chart(item.chart || [], event)}
    <div class="lm-meta">标准报价：${item.asset_type === 'etf' ? 'ETF 0.001' : item.asset_type === 'stock' ? 'A股 0.01' : '资产类型待定'}；同日原价/复权价核对后使用。结构信号用于观察。</div>
    ${snapshots.length ? `<label class="lm-history">该标的最近确认 <select data-control="event"><option value="">当前状态</option>${snapshots.toReversed().map(e => `<option value="${escape(e.event_id)}" ${event?.event_id === e.event_id ? 'selected' : ''}>${escape(e.confirmed_at)} · ${pattern(e.pattern)} · ${mode(e.mode)}</option>`).join('')}</select></label>` : ''}`;
}

export function tableRows(model) {
  const day = model.now.slice(0, 10), result = [];
  for (const item of model.items) {
    if (model.asset !== 'all' && item.asset_type !== model.asset) continue;
    const state = item.record?.state;
    if (model.tab === 'today' || model.tab === 'history') {
      for (const event of item.record?.confirmations || []) {
        if (model.tab === 'today' ? event.confirmed_at.slice(0, 10) === day :
          (!model.historyDate || event.confirmed_at.slice(0, 10) === model.historyDate)) result.push({ item, event });
      }
    } else if (model.tab === 'watch') {
      if (!item.error && !item.blocked && state?.data_asof?.slice(0, 10) === day &&
        state.decision?.veto !== 'WARMUP' && (state.decision?.impulse_recent || state.structure?.pending) &&
        !state.structure?.locked) result.push({ item, event: null });
    } else result.push({ item, event: null });
  }
  return result.sort((a, b) => (b.event?.confirmed_at || '').localeCompare(a.event?.confirmed_at || ''));
}
const CSS = `
.lm{color:inherit;font:13px/1.5 inherit;max-width:1600px;margin:0 auto;padding:24px;--lm-line:rgba(128,128,148,.22);--lm-soft:rgba(128,128,148,.055)}
.lm *{box-sizing:border-box}.lm h1{font-size:25px;margin:0;font-weight:650;letter-spacing:.02em}.lm h1 small{font-size:12px;color:#a596fa;margin-left:9px;vertical-align:middle}.lm p{margin:8px 0}.lm-meta{font-size:12px;opacity:.65;font-weight:400;line-height:1.7}.lm-head,.lm-detail-head{display:flex;gap:16px;justify-content:space-between;align-items:center}.lm-badge{background:var(--lm-soft);border:1px solid var(--lm-line);padding:7px 12px;border-radius:20px;white-space:nowrap}.lm-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:22px 0 14px}.lm input,.lm select,.lm button{color:inherit;font:inherit;border:1px solid var(--lm-line);border-radius:7px;padding:7px 10px;background:transparent;min-height:34px}.lm select option{background:var(--bg-base,#20212a);color:var(--foreground,#b9b9c9)}.lm button{cursor:pointer}.lm button:hover{background:var(--lm-soft)}.lm button:disabled{opacity:.4;cursor:default}.lm input{min-width:130px}.lm-search{display:flex;gap:6px;margin-left:auto}.lm-tabs{display:flex;gap:18px;border-bottom:1px solid var(--lm-line);margin-bottom:0;overflow-x:auto}.lm-tabs button{border:0;border-radius:0;padding:10px 2px;white-space:nowrap;opacity:.65}.lm-tabs button.active{color:#a596fa;border-bottom:2px solid #a596fa;opacity:1}.lm-table-wrap{overflow-x:auto;border-bottom:1px solid var(--lm-line)}.lm table{border-collapse:collapse;width:100%;white-space:nowrap;font-variant-numeric:tabular-nums}.lm th{font-size:11px;opacity:.6;font-weight:500;text-align:left;padding:12px 10px;background:var(--lm-soft)}.lm td{padding:13px 10px;border-top:1px solid var(--lm-line)}.lm tr[data-action]{cursor:pointer}.lm tr[data-action]:hover,.lm tr.selected{background:var(--lm-soft)}.lm .lm-code{display:block;font-size:11px;opacity:.55}.lm-buy{color:#ef6469}.lm-warning{color:#d68b30;font-size:12px}.lm-empty{padding:38px 20px;text-align:center;opacity:.65}.lm-status{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0;font-size:12px;opacity:.7}.lm-detail{border:1px solid var(--lm-line);border-radius:10px;margin-top:22px;padding:20px;background:var(--lm-soft)}.lm-detail-head{margin-bottom:18px}.lm-facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:18px 0}.lm-facts span{display:block;font-size:11px;opacity:.6}.lm-facts strong{display:block;font-size:12px;font-weight:500;overflow-wrap:anywhere}.lm svg{width:100%;height:auto;max-height:330px;display:block;margin-top:12px}.lm-history{display:flex;gap:12px;align-items:center;margin-top:18px;flex-wrap:wrap}.lm-history select{max-width:100%}.lm-foot{margin-top:18px;opacity:.55;font-size:11px}.lm-results{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.lm-pager{display:flex;gap:8px;align-items:center;margin-left:auto}.lm-error{color:#ef6469;padding:12px 0}.lm-notice{padding:10px 14px;background:var(--lm-soft);border-radius:8px;margin:14px 0}.lm .live{color:#36ab8e}.lm .historical{opacity:.6}
@media(max-width:800px){.lm{padding:14px}.lm-head{align-items:flex-start;flex-direction:column}.lm h1{font-size:21px;padding-left:38px}.lm-facts{grid-template-columns:repeat(2,minmax(0,1fr))}.lm-search{width:100%;margin-left:0}.lm-search input{flex:1;min-width:0}.lm-pager{margin-left:0}.lm-detail{padding:12px}.lm-detail-head{align-items:flex-start;flex-direction:column}}
`;
export function createView(el, handlers) {
  const root = el.ownerDocument.createElement('section');
  root.className = 'lm';
  el.append(root);
  const click = e => {
    const target = e.target.closest('[data-action]');
    if (target && root.contains(target) && !target.disabled) handlers.action(target.dataset.action, target.dataset);
  };
  const change = e => {
    if (e.target.dataset.control) handlers.change(e.target.dataset.control, e.target.value);
  };
  const key = e => { if (e.key === 'Enter' && e.target.dataset.control === 'query') handlers.action('search', {}); };
  const input = e => { if (e.target.dataset.control === 'query') handlers.change('query', e.target.value); };
  root.addEventListener('click', click); root.addEventListener('change', change); root.addEventListener('keydown', key); root.addEventListener('input', input);
  return {
    query: () => root.querySelector('[data-control="query"]')?.value || '',
    render(model) {
      const rows = tableRows(model), items = model.items;
      const selected = items.find(i => i.symbol === model.selectedSymbol);
      const event = selected?.record?.confirmations.find(e => e.event_id === model.selectedEvent);
      const latest = items.map(i => i.record?.state.data_asof || '').sort().at(-1);
      const ready = items.filter(i => i.record?.state.valid_run >= 56 && !i.error && !i.blocked).length;
      root.innerHTML = `<style>${CSS}</style><div class="lm-head"><div><h1>MA55 结构观察<small>兰 · V0.2</small></h1><p class="lm-meta">有效推动 → 均线支撑测试 → 第一根转强 · 北京时间 / 完成15m K</p></div><div class="lm-badge">${model.loading ? '正在读取 TSP 本地历史…' : model.refreshing ? '正在更新完成K…' : '页面观察已开启'}</div></div>
      <div class="lm-toolbar"><select aria-label="自选分组" data-control="group"><option value="all">全部自选</option>${model.groups.map(g => `<option value="${escape(g.id)}" ${model.group === g.id ? 'selected' : ''}>${escape(g.name)}</option>`).join('')}</select><select aria-label="资产类型" data-control="asset">${[['all', '股票 / ETF'], ['stock', '股票'], ['etf', 'ETF']].map(([v, label]) => `<option value="${v}" ${model.asset === v ? 'selected' : ''}>${label}</option>`).join('')}</select><div class="lm-pager"><button data-action="previous" ${model.page ? '' : 'disabled'}>上一页</button><span>${model.page + 1} / ${Math.max(1, Math.ceil(model.total / 20))} · 每页20只</span><button data-action="next" ${(model.page + 1) * 20 >= model.total ? 'disabled' : ''}>下一页</button></div><div class="lm-search"><input aria-label="搜索股票或ETF" data-control="query" placeholder="名称或完整代码" value="${escape(model.query)}"><button data-action="search">搜索观察</button><button data-action="reload" ${model.loading || model.refreshing ? 'disabled' : ''}>重读历史</button></div></div>
      ${model.error ? `<div class="lm-error" role="alert">${escape(model.error)}</div>` : ''}${model.cacheError ? `<div class="lm-warning">${escape(model.cacheError)}</div>` : ''}
      ${model.results.length ? `<div class="lm-results">${model.results.map(r => `<button data-action="add" data-symbol="${escape(r.symbol)}">${escape(r.name)} ${escape(r.symbol)} ＋</button>`).join('')}</div>` : ''}
      <div class="lm-status"><span>最近已验证完成K ${escape(latest || '暂无')}</span><span>本页已加载 ${items.filter(i => i.record).length}/${items.length}</span><span>连续数据充足 ${ready} · 数据不足/异常 ${items.length - ready}</span><span>数据量：手；量比无量纲</span></div>
      <div class="lm-tabs">${[['today', '今日已确认 BUY'], ['watch', '观察中'], ['history', '历史确认'], ['data', '数据状态']].map(([v, label]) => `<button data-action="tab" data-tab="${v}" class="${model.tab === v ? 'active' : ''}">${label}</button>`).join('')}</div>
      ${model.tab === 'history' ? `<div class="lm-toolbar"><label>确认日期 <input aria-label="历史确认日期" type="date" data-control="historyDate" value="${escape(model.historyDate)}"></label><span class="lm-meta">留空查看已保留的最近确认</span></div>` : ''}
      <div class="lm-table-wrap"><table><thead><tr>${['标的', '确认结束 / 来源', '支撑测试', '形态', '最新完成K收盘', '最新 MA55 / 距离', '量比 20 / 同槽', '防守点', '数据 / 风险'].map(t => `<th>${t}</th>`).join('')}</tr></thead><tbody>${rows.map(({ item, event: e }) => {
        const s = item.record?.state, d = e?.decision || s?.decision;
        const historical = e && (e.mode === 'REPLAY' || e.confirmed_at.slice(0, 10) !== model.now.slice(0, 10));
        const status = item.error || REASONS[item.blocked || s?.reason] || '等待数据';
        const risk = e && item.record.risks[e.event_id];
        return `<tr data-action="select" data-symbol="${escape(item.symbol)}" data-event="${escape(e?.event_id || '')}" class="${model.selectedSymbol === item.symbol && (model.selectedEvent || '') === (e?.event_id || '') ? 'selected' : ''} ${historical ? 'historical' : ''}"><td><b>${escape(item.name)}</b><span class="lm-code">${escape(item.symbol)}</span></td><td>${e ? `<span class="lm-buy">${escape(e.confirmed_at)}</span><span class="lm-code ${e.mode === 'LIVE' ? 'live' : ''}">${mode(e.mode)}</span>` : '—'}</td><td>${escape(d?.support_test_time || '—')}</td><td>${pattern(e?.pattern)}</td><td>${number(s?.close)}</td><td>${number(s?.decision?.ma55)}<span class="lm-code">${number(s?.decision?.close_distance_pct, 2)}%</span></td><td>${number(d?.volume_ratio20, 2)} / ${number(d?.volume_ratio_same_clock, 2)}</td><td>${number(e?.defense || d?.support_test_low)}</td><td class="${e?.risk_text ? 'lm-warning' : 'lm-meta'}">${escape(e ? (risk?.invalidated_at ? '已跌破防守' : risk?.data_pending_at ? '后续数据待定' : e.risk_text || '结构确认') : status)}<span class="lm-code">${escape(s?.data_asof || '暂无完成K')}</span></td></tr>`;
      }).join('')}</tbody></table>${rows.length ? '' : `<div class="lm-empty">${model.loading ? '正在加载所选自选集合。' : model.tab === 'today' ? '今日尚无已确认 BUY；过去的确认请查看历史。' : model.tab === 'watch' ? '当前没有已验证的当日观察结构。' : model.tab === 'data' ? '当前分组为空，可通过官方搜索添加临时观察标的。' : '本地可见范围内暂无确认。'}<div class="lm-meta">数据不足时使用 <a href="/data">TSP「数据」的分钟历史同步</a>，再点击重读历史。</div></div>`}</div>
      <div class="lm-detail">${detail(selected, event, model.uiAvailable)}</div><div class="lm-foot">仅页面打开期间观察；关闭后停止轮询，重新打开会重放本地最多20个交易日。历史重放与延迟确认均单独标明。派生缓存最多80个标的、每股120次确认，清理站点数据后从TSP重建。</div>`;
    },
    destroy() { root.removeEventListener('click', click); root.removeEventListener('change', change); root.removeEventListener('keydown', key); root.removeEventListener('input', input); root.remove(); },
  };
}
