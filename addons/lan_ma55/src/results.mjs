/** Read-only Page Pack. SQLite/backend is the only producer of MA55 events. */
const SUMMARY = 'lan_ma55_summary', DAY = 'lan_ma55_day', HISTORY = 'lan_ma55_history';
import { CSS } from './styles.mjs';
import { escapeHtml as esc, renderStatus } from './status-view.mjs';
const num = (v, digits = 0) => Number.isFinite(v) ? v.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '未知';
const mode = value => ({ REPLAY: '历史重放', LIVE: '实时确认', DELAYED: '延迟确认' }[value] || value || '—');
const phase = value => ({ RUNNING: '后台持续更新', WAITING: '等待新的完成15m', DATA_SOURCE_UNAVAILABLE: '公共行情不可用，保留已保存结果', STARTING: '后台启动中', VERIFYING_UNIVERSE: '核实完整股票候选与元信息',
  LIVE:'盘中增量', COLD:'初次历史预热', REBUILD:'历史重建', HISTORY:'历史补齐', HOST_BUSY:'礼让宿主数据任务', HOST_STATUS_UNAVAILABLE:'宿主任务状态不可核实', WAITING_NEXT_15M:'等待下一根完成15m',
  COLLECTING: '后台采集完成5m', ANALYZING: '后台计算与提交', QUOTA_WAIT: '等待滚动请求额度',
  RATE_LIMITED: '供应商429冷却', NETWORK_WAIT: '网络重试等待', WAITING_NEXT_SESSION: '等待下一交易日，缺口按计划重试',
  WAITING_NEXT_5M: '等待下一个完成5m', ERROR: '后台受阻', CALENDAR_PENDING: '等待真实交易日历' }[value] || value || '尚未收到后台状态');
const reason = value => ({ VERSION_HISTORY_INSUFFICIENT: '旧确认状态缺少足够真实历史，等待重建', FACTOR_PENDING: '因子覆盖待补', REBUILD_PENDING: '更正数据重建中', METADATA_PENDING: '证券元信息待补', WARMUP: '不足56根连续完成15m', INSUFFICIENT_WARMUP: '不足56根连续完成15m',
  EMPTY_OR_OMITTED_5M: '源未返回有效5m，原因未确认', MISSING_5M: '缺少真实5m', MISSING_COMPLETED_15M: '15m不连续，重新预热',
  HISTORICAL_FACTOR_UNAVAILABLE: '因子覆盖未确认，暂停BUY', PRICE_BASIS_CHANGED: '价格基准变化，重新预热',
  TICK_UNAVAILABLE: '报价tick未确认，暂停BUY',
  SAME_SUPPORT_SWING: '同一支撑波段已确认', CLOSE_BELOW_SUPPORT: '收盘跌破支撑', AWAIT_SUPPORT_TEST: '等待支撑测试',
  AWAIT_FIRST_RED: '等待第一根转强', NO_REAL_ADVANCE: '尚无有效推动', BUY_STRUCTURE: '结构已确认',
  INVALID_OR_INCOMPLETE_OHLCV: 'K线字段或完整性无效' }[value] || value || '—');

export function workbenchUrl(symbol, event) {
  const params = new URLSearchParams({ symbol, period: '15m' });
  if (event?.confirmed_at) params.set('event', event.confirmed_at.replace(' ', 'T'));
  if (event?.setup_at) params.set('setup', event.setup_at.replace(' ', 'T'));
  if (event?.adjust_factor > 0) params.set('factor', String(event.adjust_factor));
  if (event?.defense > 0) params.set('defense', String(event.defense));
  if (event?.event_id) params.set('event_id', event.event_id);
  if (event?.rule_version) params.set('rule_version', event.rule_version);
  if (event) params.set('source', event.source_id || 'QUANTDASH_LEGACY');
  if (event?.basis_id) params.set('basis_id', event.basis_id);
  if (event?.factor_revision !== undefined) params.set('factor_revision', String(event.factor_revision));
  if (event?.basis_revision !== undefined) params.set('basis_revision', String(event.basis_revision));
  if (event?.risk_text) params.set('risk', event.risk_text);
  return '/lan-kline?' + params;
}

export async function readSummary(api) {
  const result = await api.extDataRows(SUMMARY, { limit: 1 });
  return result.rows.length ? JSON.parse(result.rows[0].payload_json) : null;
}
export async function readDay(api, day, summary) {
  if (day === (summary.published_market_date || summary.market_date)) {
    const data = await api.extDataRows(DAY, { limit: 20000 });
    // Saved confirmations survive a universe-generation change. Only the two
    // refresh metrics use the current generation/target snapshot.
    const rows = data.rows.filter(r => r.market_date === day);
    return { ready: rows.length > 0, cycle: summary.cycle, rows };
  }
  const data = await api.extDataRows(HISTORY, { limit: 1 });
  const entry = data.rows.length ? JSON.parse(data.rows[0].payload_json).days[day] : null;
  return entry ? { ready: true, historical: true, ...entry } : { ready: false, rows: [] };
}

export function mountResults(el, ctx) {
  const root = el.ownerDocument.createElement('section'); root.className = 'mr'; el.append(root);
  let disposed = false, busy = false, timer, signature = '', shown = [];
  const model = { summary: null, date: '', pinned: false, tab: 'buy', query: '', page: 0,
    rows: [], prices: new Map(), selected: null, dayInfo: null, error: '', loading: true, workbench: 'checking' };
  function visibleRows() {
    const query = model.query.trim().toLowerCase(), out = [];
    for (const row of model.rows) {
      if (query && !`${row.symbol} ${row.name}`.toLowerCase().includes(query)) continue;
      const events = JSON.parse(row.events_json || '[]'), decision = JSON.parse(row.decision_json || '{}');
      if (model.tab === 'buy' || model.tab === 'history') {
        for (const event of events) out.push({ row, event, decision: event.decision || decision });
      } else if (model.tab !== 'watch' || row.phase === 'WATCHING') out.push({ row, decision });
    }
    return out.sort((a, b) => (b.event?.confirmed_at || '').localeCompare(a.event?.confirmed_at || '') || a.row.symbol.localeCompare(b.row.symbol));
  }
  function render() {
    if (disposed) return;
    const focus = root.contains(root.ownerDocument.activeElement) && root.ownerDocument.activeElement.dataset.control === 'query'
      ? { start: root.ownerDocument.activeElement.selectionStart, end: root.ownerDocument.activeElement.selectionEnd } : null;
    const s = model.summary || {}, cycle = s.cycle || {}, universe = s.universe || {};
    const all = visibleRows(), pages = Math.max(1, Math.ceil(all.length / 50));
    model.page = Math.min(model.page, pages - 1); shown = all.slice(model.page * 50, model.page * 50 + 50);
    const buyCount = model.rows.reduce((n, r) => n + JSON.parse(r.events_json || '[]').length, 0);
    const dates = [...new Set([s.published_market_date, s.market_date, ...(s.available_dates || [])].filter(Boolean))].sort().reverse();
    const active = model.selected, selected = active?.row, event = active?.event, d = active?.decision;
    const quote = row => model.prices.get(row.symbol);
    const price = row => {
      const q=quote(row);
      return q && q.bar_end?.slice(0,10)===model.date && Number.isFinite(q.price) && q.price>0 ? q.price.toLocaleString('zh-CN',{minimumFractionDigits:q.asset_type==='etf'?3:2,maximumFractionDigits:q.asset_type==='etf'?3:2}) : '—';
    };
    root.innerHTML = `<style>${CSS}</style>${renderStatus(s,busy,model.error)}
      <div class="mr-tools"><label>交易日 <select aria-label="结果交易日" data-control="date">${dates.map(day => `<option ${model.date===day?'selected':''}>${esc(day)}</option>`).join('') || '<option>等待历史</option>'}</select></label><button data-action="latest">最新</button><input aria-label="证券搜索" data-control="query" value="${esc(model.query)}" placeholder="名称 / 代码"><button data-action="search">搜索</button>${model.query?'<button data-action="clear">清除</button>':''}</div>
      <h2>${model.date===s.market_date?'最新交易日':!model.pinned && s.pending_publish?'上次有效交易日':'历史交易日'} ${esc(model.date||'待确认')} · ${model.dayInfo?.ready?`${buyCount} BUY`:'发布中'}${s.pending_publish && !model.pinned?' · 新结果正在发布':''}</h2>
      <div class="mr-tabs">${[['buy','BUY'],['watch','观察中'],['history','历史确认']].map(([key,label])=>`<button data-action="tab" data-tab="${key}" class="${model.tab===key?'active':''}">${label}</button>`).join('')}</div>
      <div class="mr-table"><table><thead><tr><th>名称</th><th title="所选交易日最近完成15m原价收盘；逐项悬停查看时间；无当日有效值显示—">价格</th><th title="冻结BUY的 confirmed_at，北京时间">确认时间</th></tr></thead><tbody>${shown.map(({row,event:e},i)=>{
        const q=quote(row);
        return `<tr data-row="${i}" tabindex="0" aria-label="打开${esc(row.name)}K线"><td><a class="mr-kline" data-stock-link href="${esc(workbenchUrl(row.symbol,e))}">${esc(row.name)}</a>${q?.asset_type==='etf'?'<span class="mr-type">ETF</span>':''}<span class="mr-code">${esc(row.symbol)}</span></td><td class="mr-price" title="${esc(q?.bar_end?.slice(0,10)===model.date?`${q.bar_end} · 原价完成15m收盘 · ${q.source_id}`:'所选日无有效完成15m价格')}">${price(row)}</td><td>${esc(e?.confirmed_at?.slice(0,16)||'—')}</td></tr>`;
      }).join('')}</tbody></table>${shown.length?'':`<div class="mr-empty">${model.loading?'正在读取…':!model.dayInfo?.ready?'所选日结果发布中':model.tab==='watch'?'暂无观察结构':'所选日暂无已确认 BUY'}</div>`}</div>
      <div class="mr-pager"><button data-action="previous" ${model.page===0?'disabled':''}>上一页</button><span>${model.page+1} / ${pages} · ${all.length}条</span><button data-action="next" ${model.page+1>=pages?'disabled':''}>下一页</button></div>
      <details class="mr-diagnostics" ${model.diagnostics?'open':''}><summary>数据状态 / 详情</summary><p>${esc(phase(s.phase))} · ${esc(cycle.bar_end||'')} · 股票 ${num(universe.stock)} / ETF ${num(universe.etf)}</p><p>有效 ${num(cycle.valid_symbols)} · 不足 ${num(cycle.insufficient_symbols)} · 缺口 ${num(cycle.gap_symbols)} · 失败 ${num(cycle.failed_symbols)} · 缺tick ${num(universe.unpriced)}</p><p>5m请求 ${num(s.requests?.minute_requests)} / HTTP ${num(s.requests?.http_requests)} · 待预热 ${num(s.pending_symbols)}</p><button data-action="tab" data-tab="data">查看证券状态</button><details><summary>后台状态</summary><pre>${esc(JSON.stringify(s,null,2))}</pre></details><div>${shown.map(({row,event:e},i)=>`<button data-detail="${i}">${esc(row.name)} · ${esc(reason(row.reason))}${e?' · '+esc(mode(e.mode)):''}</button>`).join('')}</div>
      ${selected?`<section class="mr-panel mr-selected"><h2>${esc(selected.name)} ${esc(selected.symbol)}</h2><p>${esc(reason(selected.reason))}</p><p>支撑测试 ${esc(event?.setup_at||d?.support_test_time||'—')} · 确认 ${esc(event?.confirmed_at||'尚未确认')} · ${esc(mode(event?.mode))}</p><p>首次看到 ${esc(event?.first_seen_at||'—')} · ${esc(event?.risk_text||'')}</p><pre>${esc(JSON.stringify({event,decision:d},null,2))}</pre><button data-action="preview" ${ctx.ui?.openStockPreview?'':'disabled'}>官方个股详情 ↗</button> ${model.workbench==='enabled'?`<a href="${esc(workbenchUrl(selected.symbol,event))}" class="mr-kline">15 分钟 K ↗</a>`:'<span>安装并启用独立分钟K工作台后可打开K线。</span>'}</section>`:''}</details>`;
    if (focus) {
      const input = root.querySelector('[data-control="query"]');
      input.focus({preventScroll:true}); input.setSelectionRange(focus.start,focus.end);
    }
  }

  async function refresh(force = false) {
    if (disposed || busy) return;
    busy = true; render();
    try {
      const summary = await readSummary(ctx.api);
      if (disposed) return;
      model.summary = summary;
      const prices = await ctx.api.extDataRows('lan_ma55_prices_v1', {limit:1});
      if (disposed) return;
      const projection = prices.rows[0]?.payload_json ? JSON.parse(prices.rows[0].payload_json) : {};
      model.prices = new Map((projection.rows || []).map(q=>[q.symbol,q]));
      if (summary) {
        const date = !model.pinned || !model.date ? summary.published_market_date || summary.market_date || '' : model.date;
        const key = `${date}:${summary.generation}:${summary.revision}:${summary.day_published_revision || ''}:${summary.history_published_at || ''}`;
        if (date && (force || key !== signature)) {
          const info = await readDay(ctx.api, date, summary);
          if (disposed) return;
          if (info.ready || model.pinned || !model.rows.length) {
            model.date = date; model.dayInfo = info; model.rows = info.rows;
            if (info.ready) signature = key;
          }
        }
      }
      model.error = '';
    } catch (error) { if (!disposed) model.error = `后台结果读取失败：${error.message || error}；保留上一份可见结果。`; }
    finally { busy = false; model.loading = false; render(); }
  }
  const click = async e => {
    const stockLink=e.target.closest('[data-stock-link]');
    if(stockLink) { if(model.workbench!=='enabled') { e.preventDefault(); model.error='分钟K工作台未就绪，请在设置中启用；可使用下方官方个股详情。'; model.selected=shown[Number(stockLink.closest('[data-row]').dataset.row)]; render(); } return; }
    const detail = e.target.closest('[data-detail]');
    if (detail) { model.selected=shown[Number(detail.dataset.detail)]; model.diagnostics=true; render(); return; }
    const row = e.target.closest('[data-row]');
    if (row) { row.querySelector('[data-stock-link]').click(); return; }
    const button = e.target.closest('[data-action]'); if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (action === 'refresh') { await refresh(true); return; }
    if (action === 'tab') { model.tab = button.dataset.tab; model.page = 0; if(model.tab==='data') model.diagnostics=true; }
    else if (action === 'search') model.page = 0;
    else if (action === 'clear') { model.query=''; model.page=0; }
    else if (action === 'previous' || action === 'next') model.page += action === 'next' ? 1 : -1;
    else if (action === 'latest') { model.pinned = false; model.page = 0; await refresh(true); return; }
    else if (action === 'preview' && model.selected) {
      const selected = model.selected.row;
      if (!ctx.ui?.openStockPreview({ symbol: selected.symbol, name: selected.name,
        navList: shown.map(v => ({ symbol: v.row.symbol, name: v.row.name })) })) model.error = '宿主未能打开个股详情。';
    }
    render();
  };
  const input = e => { if (e.target.dataset.control === 'query') model.query = e.target.value; };
  const change = e => {
    if (e.target.dataset.control === 'date') { model.date = e.target.value; model.pinned = true; model.page = 0; model.selected = null; model.rows = []; model.dayInfo = null; model.loading = true; void refresh(true); }
  };
    const key = e => { if ((e.key==='Enter'||e.key===' ') && e.target.matches('[data-row]')) { e.preventDefault(); e.target.querySelector('[data-stock-link]').click(); return; } if (e.key === 'Enter' && e.target.dataset.control === 'query') { model.page = 0; render(); } };
  const toggle = e => { if(e.target.classList?.contains('mr-diagnostics')) model.diagnostics=e.target.open; };
  root.addEventListener('toggle',toggle,true);
  root.addEventListener('click', click); root.addEventListener('input', input); root.addEventListener('change', change); root.addEventListener('keydown', key);
  render(); void refresh();
  if (typeof ctx.api.extPagesList === 'function') {
    ctx.api.extPagesList().then(pages => {
      if (disposed) return;
      model.workbench = pages.some(p => p.enabled && !p.error && (p.path === '/lan-kline' || p.managed_by === 'lan.kline')) ? 'enabled' : 'disabled'; render();
    }).catch(() => { if (!disposed) { model.workbench = 'unknown'; render(); } });
  } else model.workbench = 'unknown';
  timer = setInterval(() => { if (el.ownerDocument.visibilityState !== 'hidden') void refresh(); }, 10_000);
  return () => { disposed = true; clearInterval(timer); root.removeEventListener('toggle',toggle,true); root.removeEventListener('click', click); root.removeEventListener('input', input); root.removeEventListener('change', change); root.removeEventListener('keydown', key); root.remove(); };
}
