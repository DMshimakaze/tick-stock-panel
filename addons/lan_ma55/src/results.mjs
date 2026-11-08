/** Read-only Page Pack. SQLite/backend is the only producer of MA55 events. */
const SUMMARY = 'lan_ma55_summary', DAY = 'lan_ma55_day', HISTORY = 'lan_ma55_history';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, digits = 0) => Number.isFinite(v) ? v.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '未知';
const mode = value => ({ REPLAY: '历史重放', LIVE: '实时确认', DELAYED: '延迟确认' }[value] || value || '—');
const phase = value => ({ STARTING: '后台启动中', VERIFYING_UNIVERSE: '核实完整股票候选与元信息',
  COLLECTING: '后台采集完成5m', ANALYZING: '后台计算与提交', QUOTA_WAIT: '等待滚动请求额度',
  RATE_LIMITED: '供应商429冷却', NETWORK_WAIT: '网络重试等待', WAITING_NEXT_SESSION: '等待下一交易日，缺口按计划重试',
  WAITING_NEXT_5M: '等待下一个完成5m', ERROR: '后台受阻', CALENDAR_PENDING: '等待真实交易日历' }[value] || value || '尚未收到后台状态');
const reason = value => ({ WARMUP: '不足56根连续完成15m', INSUFFICIENT_WARMUP: '不足56根连续完成15m',
  EMPTY_OR_OMITTED_5M: '源未返回有效5m，原因未确认', MISSING_5M: '缺少真实5m', MISSING_COMPLETED_15M: '15m不连续，重新预热',
  HISTORICAL_FACTOR_UNAVAILABLE: '因子覆盖未确认，暂停BUY', PRICE_BASIS_CHANGED: '价格基准变化，重新预热',
  TICK_UNAVAILABLE: '报价tick未确认，暂停BUY',
  SAME_SUPPORT_SWING: '同一支撑波段已确认', CLOSE_BELOW_SUPPORT: '收盘跌破支撑', AWAIT_SUPPORT_TEST: '等待支撑测试',
  AWAIT_FIRST_RED: '等待第一根转强', NO_REAL_ADVANCE: '尚无有效推动', BUY_STRUCTURE: '结构已确认',
  INVALID_OR_INCOMPLETE_OHLCV: 'K线字段或完整性无效' }[value] || value || '—');

export function workbenchUrl(symbol, event) {
  const params = new URLSearchParams({ symbol, period: '15m' });
  if (event?.confirmed_at) params.set('event', event.confirmed_at.replace(' ', 'T'));
  return '/lan-kline?' + params;
}

export async function readSummary(api) {
  const result = await api.extDataRows(SUMMARY, { limit: 1 });
  return result.rows.length ? JSON.parse(result.rows[0].payload_json) : null;
}
export async function readDay(api, day, summary) {
  if (day === summary.market_date) {
    const data = await api.extDataRows(DAY, { limit: 20000 });
    return { ready: true, cycle: summary.cycle, rows: data.rows.filter(r => r.market_date === day && r.generation === summary.generation) };
  }
  const data = await api.extDataRows(HISTORY, { limit: 1 });
  const entry = data.rows.length ? JSON.parse(data.rows[0].payload_json).days[day] : null;
  return entry ? { ready: true, historical: true, ...entry } : { ready: false, rows: [] };
}

const CSS = `
.mr{padding:24px;max-width:1540px;margin:auto;color:var(--text-primary,#c7c7d2);font:13px/1.6 system-ui;--line:rgba(130,130,150,.18);--soft:rgba(130,130,150,.05)}.mr *{box-sizing:border-box}.mr h1{font-size:24px;margin:0;color:var(--text-primary,#eee)}.mr h1 small{font-size:12px;color:#a596fa;margin-left:10px}.mr h2{font-size:15px;margin:0}.mr p{margin:6px 0}.mr button,.mr input,.mr select{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:7px;min-height:34px;padding:6px 10px}.mr button{cursor:pointer}.mr button:disabled{opacity:.4;cursor:default}.mr select option{color:var(--text-primary,#ccc);background:var(--bg-base,#17181e)}.mr-header,.mr-tools,.mr-tabs{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.mr-header{justify-content:space-between;margin-bottom:18px}.mr-meta{font-size:11px;opacity:.65;overflow-wrap:anywhere}.mr-panel{border:1px solid var(--line);border-radius:10px;background:var(--soft);padding:16px;margin:14px 0}.mr-facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:14px 0}.mr-facts span{display:block;opacity:.6;font-size:11px}.mr-facts strong{font-size:13px;font-weight:550;overflow-wrap:anywhere}.mr-warning{border-left:3px solid #d68b30}.mr-error{color:#ef7478;padding:8px 0;overflow-wrap:anywhere}.mr-tabs{border-bottom:1px solid var(--line);margin-top:20px;gap:18px}.mr-tabs button{border:0;border-radius:0;white-space:nowrap;padding:10px 0;opacity:.65}.mr-tabs .active{color:#ac9cff;border-bottom:2px solid #ac9cff;opacity:1}.mr-tools{margin:15px 0}.mr-tools input{min-width:140px;flex:1}.mr-table{overflow-x:auto;border:1px solid var(--line);border-radius:9px}.mr table{width:100%;border-collapse:collapse;white-space:nowrap;font-variant-numeric:tabular-nums}.mr th{font-size:11px;font-weight:400;opacity:.6;text-align:left;padding:10px;background:var(--soft)}.mr td{padding:12px 10px;border-top:1px solid var(--line)}.mr tr[data-row]{cursor:pointer}.mr tr[data-row]:hover{background:var(--soft)}.mr-kline{display:inline-block;margin-left:10px;color:#ac9cff;text-decoration:underline;padding:6px}.mr-code{display:block;font-size:11px;opacity:.6}.mr-buy{color:#ef7478}.mr-empty{text-align:center;padding:30px 14px;opacity:.75}.mr-pager{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin:12px 0}.mr-selected{border-left:3px solid #a596fa}.mr-foot{font-size:11px;opacity:.6;margin:16px 0}
@media(max-width:800px){.mr{padding:14px}.mr h1{font-size:20px;padding-left:38px}.mr-facts{grid-template-columns:repeat(2,minmax(0,1fr))}.mr-header{align-items:flex-start}.mr-tools input{min-width:0;width:100%}.mr-tools{align-items:stretch;display:grid;grid-template-columns:minmax(0,1fr) auto}.mr-tabs{gap:12px}.mr-panel{padding:12px}}
`;

export function mountResults(el, ctx) {
  const root = el.ownerDocument.createElement('section'); root.className = 'mr'; el.append(root);
  let disposed = false, busy = false, timer, signature = '', shown = [];
  const model = { summary: null, date: '', pinned: false, tab: 'buy', query: '', page: 0,
    rows: [], selected: null, dayInfo: null, error: '', loading: true, workbench: 'checking' };
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
    const s = model.summary || {}, cycle = s.cycle || {}, universe = s.universe || {};
    const all = visibleRows(), pages = Math.max(1, Math.ceil(all.length / 50));
    model.page = Math.min(model.page, pages - 1); shown = all.slice(model.page * 50, model.page * 50 + 50);
    const buyCount = model.rows.reduce((n, r) => n + JSON.parse(r.events_json || '[]').length, 0);
    const dates = [...new Set([s.market_date, ...(s.available_dates || [])].filter(Boolean))].sort().reverse();
    const active = model.selected, selected = active?.row, event = active?.event, d = active?.decision;
    const fact = (label, value) => `<div><span>${label}</span><strong>${esc(value)}</strong></div>`;
    root.innerHTML = `<style>${CSS}</style><div class="mr-header"><div><h1>MA55 结构观察<small>兰 · V0.2.9</small></h1><div class="mr-meta">QuantDash Pro → 后台5m→15m → 冻结V0.2 → SQLite → 官方扩展数据</div></div><button data-action="refresh" ${busy ? 'disabled' : ''}>${busy ? '正在读取后台…' : '刷新后台结果'}</button></div>
      <section class="mr-panel" aria-label="MA55扫描状态"><h2>MA55扫描状态 · ${esc(phase(s.phase))}</h2><div class="mr-facts">${[
        ['最新交易日', s.market_date || '等待真实行情'], ['数据截至', s.data_asof || '尚无完成15m'],
        ['本轮处理 / 核实股票分母', `${num(cycle.processed_symbols)} / ${num(cycle.expected_symbols ?? universe.expected)}`],
        ['有效 / 预热不足', `${num(cycle.valid_symbols)} / ${num(cycle.insufficient_symbols)}`],
        ['缺口 / 失败', `${num(cycle.gap_symbols)} / ${num(cycle.failed_symbols)}`],
        ['真实HTTP / 分钟批量 / 429', `${num(s.requests?.http_requests)} / ${num(s.requests?.minute_requests)} / ${num(s.requests?.http_429_count)}`],
        ['本批耗时 / 额度等待', `${num(s.batch_ms, 1)}ms / ${num(s.wait_seconds, 1)}秒`],
        ['最近请求 / 轮次完成', `${s.last_request_at || '尚无'} / ${cycle.completed_at || '进行中'}`],
      ].map(([label, value]) => fact(label, value)).join('')}</div>
      <div class="mr-meta">后台${s.backend_scanning ? '已启动，关闭网页仍运行' : '状态待核实或已停止'} · 5m预算24次/滚动60秒，每批最多100股 · 股票元信息 ${num(s.metadata_processed)} / ${num(s.metadata_expected)} · SQLite逐股状态 ${num(s.symbol_states)}</div></section>
      ${universe.unclassified || universe.listing_status_unconfirmed ? `<div class="mr-panel mr-warning">股票池范围待确认：未分类 ${num(universe.unclassified)}，非快照股票中上市状态未提供 ${num(universe.listing_status_unconfirmed)}。已确认股票类型 ${num(universe.expected)}，其中缺tick ${num(universe.unpriced)} 仍计入分母并暂停BUY；此范围不标为完整上市全集验收。<div class="mr-meta">源股票快照 ${num(universe.quote_pool)}；名称映射候选 ${num(universe.candidates)}；ETF/指数等已排除 ${num(universe.excluded)}。缺K按缺口计，不按停牌或0 BUY计。</div></div>` : ''}
      ${model.error || s.error || s.publisher_error ? `<div class="mr-error" role="alert">${esc([model.error, s.error, s.publisher_error].filter(Boolean).join('；'))}</div>` : ''}
      <div class="mr-tools"><label>结果交易日 <select aria-label="结果交易日" data-control="date">${dates.map(day => `<option ${model.date === day ? 'selected' : ''}>${esc(day)}</option>`).join('') || '<option>等待历史</option>'}</select></label><button data-action="latest">跟随最新交易日</button><input aria-label="全市场股票搜索" data-control="query" value="${esc(model.query)}" placeholder="股票名称或完整代码"><button data-action="search">搜索</button></div>
      <h2>${model.date === s.market_date ? '最新交易日' : '历史交易日'} ${esc(model.date || '待确认')} · ${model.dayInfo?.ready ? `${buyCount} BUY` : '历史结果发布中'}</h2>
      <p class="mr-meta">${model.dayInfo?.historical ? `历史保留BUY、观察与异常行；该日处理 ${num(model.dayInfo.cycle?.processed_symbols)} / ${num(model.dayInfo.cycle?.expected_symbols)}，有效 ${num(model.dayInfo.cycle?.valid_symbols)}，不足 ${num(model.dayInfo.cycle?.insufficient_symbols)}，缺口 ${num(model.dayInfo.cycle?.gap_symbols)}，失败 ${num(model.dayInfo.cycle?.failed_symbols)}。` : '展示当前后台已发布结果；本轮未结束时，BUY数量随真实提交更新。'} 原始确认时间和 REPLAY / DELAYED / LIVE 来源保留。</p>
      <div class="mr-tabs">${[['buy', '最新交易日 BUY'], ['watch', '观察中'], ['history', '历史确认'], ['data', '数据状态']].map(([key, label]) => `<button data-action="tab" data-tab="${key}" class="${model.tab === key ? 'active' : ''}">${label}</button>`).join('')}</div>
      <div class="mr-table"><table><thead><tr>${['股票', '确认结束 / 来源', '支撑测试 / 形态', '完成15m / MA55', '量比20 / 同槽', '后台状态'].map(t => `<th>${t}</th>`).join('')}</tr></thead><tbody>${shown.map(({ row, event: e, decision: dd }, i) => `<tr data-row="${i}"><td><b>${esc(row.name)}</b><span class="mr-code">${esc(row.symbol)}</span></td><td class="${e ? 'mr-buy' : ''}">${esc(e?.confirmed_at || '—')}<span class="mr-code">${esc(mode(e?.mode))}</span></td><td>${esc(e?.setup_at || dd.support_test_time || '—')}<span class="mr-code">${e?.pattern === 'SINGLE_BAR' ? '单K收回' : e?.pattern === 'MULTI_BAR' ? '多K测试' : '—'}</span></td><td>${esc(row.bar_end || '暂无完成K')}<span class="mr-code">MA55 ${num(dd.ma55, 3)}</span></td><td>${num(dd.volume_ratio20, 2)} / ${num(dd.volume_ratio_same_clock, 2)}</td><td>${esc(reason(row.reason))}<span class="mr-code">${esc(row.processed_at)}</span></td></tr>`).join('')}</tbody></table>${shown.length ? '' : `<div class="mr-empty">${model.loading ? '正在读取后台派生结果。' : !model.dayInfo?.ready ? '后台正在发布所选历史，等待真实结果。' : model.tab === 'buy' || model.tab === 'history' ? `${esc(model.date)} 已发布结果中没有已确认 BUY。可查看数据状态或选择更早交易日。` : model.tab === 'watch' ? '所选交易日暂无观察结构。' : '当前筛选没有记录。'}</div>`}</div>
      <div class="mr-pager"><button data-action="previous" ${model.page === 0 ? 'disabled' : ''}>上一页</button><span>${model.page + 1} / ${pages} · ${all.length}条</span><button data-action="next" ${model.page + 1 >= pages ? 'disabled' : ''}>下一页</button></div>
      ${selected ? `<section class="mr-panel mr-selected"><h2>${esc(selected.name)} ${esc(selected.symbol)}</h2><p>${esc(reason(selected.reason))}</p><p>支撑测试 ${esc(event?.setup_at || d?.support_test_time || '—')} · 确认 ${esc(event?.confirmed_at || '尚未确认')} · ${esc(mode(event?.mode))}</p><p>首次看到 ${esc(event?.first_seen_at || '—')} · 行情截至 ${esc(event?.data_asof || selected.bar_end || '未知')}</p><p>防守点 ${num(event?.defense || d?.support_test_low, 3)} · ${esc(event?.risk_text || '')}</p><button data-action="preview" ${ctx.ui?.openStockPreview ? '' : 'disabled'}>官方个股详情 ↗</button> ${model.workbench === "enabled" ? `<a href="${esc(workbenchUrl(selected.symbol, event))}" class="mr-kline">15 分钟 K ↗</a>` : `<span class="mr-meta">${model.workbench === "checking" ? "正在确认分钟K工作台…" : model.workbench === "disabled" ? "安装并启用独立分钟K工作台后，可从此处打开15分钟K。" : "无法读取工作台启用状态，请在设置中确认。"}</span>`}</section>` : ''}
      <div class="mr-foot">后台SQLite为唯一权威；页面只读取官方扩展数据，不采集行情、不计算BUY、不使用IndexedDB。历史保留60个交易日（后台可配置）；时间为北京时间。</div>`;
  }
  async function refresh(force = false) {
    if (disposed || busy) return;
    busy = true; render();
    try {
      const summary = await readSummary(ctx.api);
      if (disposed) return;
      model.summary = summary;
      if (summary) {
        if (!model.pinned || !model.date) model.date = summary.market_date || '';
        const key = `${model.date}:${summary.generation}:${summary.revision}:${summary.history_published_at || ''}`;
        if (model.date && (force || key !== signature)) {
          const info = await readDay(ctx.api, model.date, summary);
          if (disposed) return;
          model.dayInfo = info; model.rows = info.rows; signature = key;
        }
      }
      model.error = '';
    } catch (error) { if (!disposed) model.error = `后台结果读取失败：${error.message || error}；保留上一份可见结果。`; }
    finally { busy = false; model.loading = false; render(); }
  }
  const click = async e => {
    const row = e.target.closest('[data-row]');
    if (row) { model.selected = shown[Number(row.dataset.row)]; render(); return; }
    const button = e.target.closest('[data-action]'); if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (action === 'refresh') { await refresh(true); return; }
    if (action === 'tab') { model.tab = button.dataset.tab; model.page = 0; }
    else if (action === 'search') model.page = 0;
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
  const key = e => { if (e.key === 'Enter' && e.target.dataset.control === 'query') { model.page = 0; render(); } };
  root.addEventListener('click', click); root.addEventListener('input', input); root.addEventListener('change', change); root.addEventListener('keydown', key);
  render(); void refresh();
  if (typeof ctx.api.extPagesList === 'function') {
    ctx.api.extPagesList().then(pages => {
      if (disposed) return;
      model.workbench = pages.some(p => p.enabled && !p.error && p.path === '/lan-kline') ? 'enabled' : 'disabled'; render();
    }).catch(() => { if (!disposed) { model.workbench = 'unknown'; render(); } });
  } else model.workbench = 'unknown';
  timer = setInterval(() => { if (el.ownerDocument.visibilityState !== 'hidden') void refresh(); }, 10_000);
  return () => { disposed = true; clearInterval(timer); root.removeEventListener('click', click); root.removeEventListener('input', input); root.removeEventListener('change', change); root.removeEventListener('keydown', key); root.remove(); };
}
