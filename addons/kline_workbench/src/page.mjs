import { aggregateMinutes, dailyCandles, beijing, epoch, PERIODS } from './aggregate.mjs';
import { DEFAULT_MAS, movingAverages, eventAnchor } from './indicators.mjs';
import { createReader, fullSymbol, searchInstruments, liveTailAllowed } from './data.mjs';
import { createWorkbenchChart } from './chart.mjs';

const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const PREFS = 'lan.kline.preferences.v1';
export function parseLink(url) {
  const q = new URL(url).searchParams, errors = [];
  let symbol = q.get('symbol'), event = q.get('event'), period = q.get('period') || '15m';
  if (symbol && !fullSymbol(symbol)) { errors.push('URL需要带.SH/.SZ/.BJ的完整代码'); symbol = null; }
  if (!PERIODS.includes(period)) { errors.push('URL级别不支持，已选择15m'); period = '15m'; }
  if (event) {
    event = event.replace('T', ' ');
    if (!/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(event) || beijing(epoch(event) * 1000) !== event) {
      errors.push('信号时间无效，未添加标记'); event = null;
    }
  }
  return { symbol: symbol ? fullSymbol(symbol) : null, event, period, errors };
}
const CSS = `
.kw{max-width:1600px;margin:auto;padding:24px;color:var(--text-primary,#c7c7d2);font:13px/1.6 system-ui;--line:rgba(130,130,150,.2)}.kw *{box-sizing:border-box}.kw h1{font-size:24px;margin:0}.kw h1 small{font-size:11px;color:#a995f4;margin-left:10px}.kw p{margin:6px 0}.kw a{color:#a995f4}.kw button,.kw input,.kw select{font:inherit;color:inherit;border:1px solid var(--line);background:var(--bg-base,#17181e);padding:6px 10px;border-radius:6px;min-height:34px;max-width:100%}.kw button{cursor:pointer}.kw button:disabled{opacity:.45;cursor:default}.kw input[type=checkbox]{min-height:0;accent-color:#a995f4}.kw input[type=number]{width:78px}.kw-head,.kw-controls,.kw-search,.kw-ma-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.kw-head{justify-content:space-between;margin-bottom:16px}.kw-search input{flex:1;min-width:180px}.kw-search{margin:12px 0}.kw-results{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.kw-controls{margin:14px 0}.kw-muted{font-size:11px;opacity:.65;overflow-wrap:anywhere}.kw-panel{padding:12px 14px;border:1px solid var(--line);border-radius:9px;background:rgba(130,130,150,.04);margin:12px 0}.kw-status{font-variant-numeric:tabular-nums}.kw-error{color:#ee7b83;overflow-wrap:anywhere}.kw-warning{color:#d8b06d}.kw-ma-list{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.kw-ma-row{border:1px solid var(--line);border-radius:6px;gap:5px;padding:4px 6px}.kw-ma-row input[type=number]{width:64px;padding:3px}.kw-ma-row button{min-height:26px;padding:1px 6px}.kw-chart{height:520px;position:relative;color:var(--text-primary,#c7c7d2);min-width:0}.kw-readout{min-height:62px;font-variant-numeric:tabular-nums;display:flex;gap:4px 16px;flex-wrap:wrap;align-content:flex-start}.kw-readout b{font-weight:550}.kw-anchor{margin:10px 0;overflow-wrap:anywhere}.kw-footer{margin-top:12px;font-size:11px;opacity:.65}.kw details summary{cursor:pointer}.kw [role=alert]:empty{display:none}
@media(max-width:650px){.kw{padding:14px}.kw h1{font-size:20px;padding-left:38px}.kw-chart{height:460px}.kw-controls{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.kw-controls label{display:flex;flex-wrap:wrap;align-items:center;gap:4px}.kw-controls select{min-width:0}.kw-search input{min-width:0}.kw-head{align-items:flex-start}.kw-panel{padding:10px}}
`;
function preferences(win) {
  try {
    const p = JSON.parse(win.localStorage.getItem(PREFS));
    if (p && Array.isArray(p.mas) && p.mas.length <= 12 && new Set(p.mas.map(m => m.period)).size === p.mas.length
      && p.mas.every(m => Number.isInteger(m.period) && m.period >= 1 && m.period <= 2000 && typeof m.enabled === 'boolean')
      && Number.isInteger(p.visible) && p.visible >= 0 && p.visible <= 10000) return p;
  } catch { /* Browser storage can be unavailable; only preferences are optional. */ }
  return { mas: DEFAULT_MAS.map(m => ({ ...m })), visible: 120 };
}
export function mountWorkbench(el, ctx, { chartFactory = createWorkbenchChart, now = () => beijing() } = {}) {
  const win = el.ownerDocument.defaultView, link = parseLink(win.location.href), prefs = preferences(win);
  const reader = createReader(ctx.api), root = el.ownerDocument.createElement('section'); root.className = 'kw'; el.append(root);
  root.innerHTML = `<style>${CSS}</style><header class="kw-head"><h1>分钟 K 工作台<small>兰 · 1.0.1</small></h1><button data-action="official">官方个股详情 ↗</button></header>
    <p class="kw-muted">股票 / ETF · 北京时间 · 均线周期按当前级别的K线根数计算</p>
    <form class="kw-search"><input aria-label="股票或ETF搜索" placeholder="名称或完整代码，如600519.SH" autocomplete="off"><button>搜索 / 打开</button></form><div class="kw-results" aria-label="搜索结果"></div>
    <div class="kw-controls"><label>K线级别 <select aria-label="K线级别">${PERIODS.map(p => `<option value="${p}">${p === '1d' ? '日 K' : p}</option>`).join('')}</select></label>
    <label>初始显示 <select aria-label="初始显示根数">${[40,80,120,240,0].map(n => `<option value="${n}">${n || '全部'}${n ? '根' : ''}</option>`).join('')}<option value="custom">自定义</option></select></label>
    <label>自定义根数 <input type="number" min="10" max="10000" aria-label="自定义可见根数"></label><button data-action="range">应用视窗</button>
    <button data-action="refresh">刷新本地读取</button><button data-action="tail" title="仅在已确认交易日的连续竞价时段，按需请求当前单股">更新盘中尾部</button></div>
    <div class="kw-error" role="alert"></div><div class="kw-panel kw-status" aria-live="polite"></div>
    <details open><summary>自定义均线 · 当前级别根数</summary><div class="kw-ma-list"></div><div class="kw-ma-row"><label>新增周期 <input type="number" min="1" max="2000" value="21" aria-label="新增MA周期"></label><button data-action="add-ma">添加 MA</button></div></details>
    <div class="kw-anchor"></div><div class="kw-readout" aria-label="十字光标读数"></div><div class="kw-chart" role="img" aria-label="蜡烛、均线和联动成交量图"></div>
    <p class="kw-warning kw-availability"></p><p class="kw-muted kw-message"></p><footer class="kw-footer">滚轮缩放，拖动平移，十字光标读取价格/成交量/MA。仅持久化图表偏好，行情只在本页面会话内缓存。<br>MA55信号来自独立QuantDash后台；这里使用TSP官方投影，未宣称两套输入逐根相同。图表由 <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a> 绘制。</footer>`;
  const $ = s => root.querySelector(s);
  const model = { ...prefs, period: link.period, event: link.event, eventSymbol: link.symbol, selected: null, data: null, matches: [], loading: false, error: link.errors.join('；') };
  let disposed = false, revision = 0, searchRevision = 0, plot, current = null;
  const format = (value, digits = model.data?.asset_type === 'etf' ? 3 : 2) => Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }) : '—';
  function readout(bar) {
    if (disposed) return;
    if (!bar.complete) { $('.kw-readout').textContent = `${bar.timestamp} · 缺少完整输入，不显示价格或MA`; return; }
    $('.kw-readout').innerHTML = [`<b>${esc(bar.timestamp)}</b>`, ...[['开',bar.open],['高',bar.high],['低',bar.low],['收',bar.close],['量(手)',bar.volume],['额(元)',bar.amount]].map(([k,v]) => `<span>${k} ${format(v)}</span>`),
      ...bar.averages.map(([n,v]) => `<span>MA${n} <b>${format(v, 3)}</b></span>`)].join('');
  }
  try { plot = chartFactory($('.kw-chart'), readout); } catch (error) { model.error = `图表初始化失败：${error.message}`; }
  function save() {
    try { win.localStorage.setItem(PREFS, JSON.stringify({ mas: model.mas, visible: model.visible })); }
    catch { model.error = '当前浏览器无法保存图表偏好，本页仍可使用'; }
  }
  function writeUrl() {
    const q = new URLSearchParams({ symbol: model.selected.symbol, period: model.period });
    if (model.event) q.set('event', model.event.replace(' ', 'T'));
    win.history.replaceState(null, '', '/lan-kline?' + q);
  }
  function controls() {
    $('[aria-label="K线级别"]').value = model.period;
    $('[aria-label="初始显示根数"]').value = [0,40,80,120,240].includes(model.visible) ? String(model.visible) : 'custom';
    $('[aria-label="自定义可见根数"]').value = model.visible || '';
    $('.kw-ma-list').innerHTML = model.mas.map((m,i) => `<div class="kw-ma-row"><input type="checkbox" aria-label="显示MA${m.period}" data-ma-toggle="${i}" ${m.enabled ? 'checked' : ''}><label>MA <input type="number" min="1" max="2000" aria-label="MA${m.period}周期" data-ma-period="${i}" value="${m.period}"></label><button data-ma-remove="${i}" aria-label="删除MA${m.period}">×</button></div>`).join('');
    $('[data-action="official"]').disabled = !model.selected || !ctx.ui?.openStockPreview;
    $('[data-action="refresh"]').disabled = !model.selected || model.loading;
    $('[aria-label="自定义可见根数"]').disabled = model.loading;
    $('[data-action="range"]').disabled = model.loading;
    $('[data-action="tail"]').disabled = !model.data || model.loading || !liveTailAllowed(model.data, now());
    $('[role="alert"]').textContent = model.error;
  }
  function update(reset = false) {
    if (disposed) return;
    controls();
    const data = model.data, minute = model.period !== '1d';
    const calendar = data && !data.errors.daily ? data.daily.map(r => r.date) : [];
    current = data ? minute ? aggregateMinutes(data.sessions, model.period, now(), calendar) : dailyCandles(data.daily, now())
      : { bars: [], validCount: 0, continuous: 0, gaps: 0, pending: 0, dates: [] };
    const enabled = model.mas.filter(m => m.enabled).map(m => m.period), averages = movingAverages(current.bars, enabled);
    const anchor = eventAnchor(current.bars, model.event, model.period);
    if (!data && model.event) anchor.message = '尚未取得当前标的历史，未添加信号标记';
    plot?.update({ bars: current.bars, averages, anchor, asset: data?.asset_type, visible: model.visible }, reset);
    if (!current.validCount) $('.kw-readout').textContent = '暂无完整K读数';
    const source = minute ? 'TSP 本地分钟行情（官方投影）' : 'TSP 官方日K投影';
    $('.kw-status').innerHTML = `<b>${esc(model.selected ? `${data?.name || model.selected.name} ${model.selected.symbol}` : '搜索或输入股票 / ETF')}</b> · ${model.period === '1d' ? '日 K' : model.period}${model.loading ? ' · 正在读取当前标的…' : ''}<br>
      来源：${source}${data?.tailSource ? '；当前日尾部经官方按需接口更新' : ''} · 数据截至 ${esc(current.dataAsOf || '尚无完整K')}<br>
      实际取得 ${current.validCount} 根完整K / 缺口 ${current.gaps} / 未完成区间 ${current.pending} · 末段连续 ${current.continuous} 根 · 初始视窗 ${model.visible || '全部'}${model.visible ? '根' : ''}
      ${minute ? `<br>实际本地 ${data?.sessions.length || 0} 个交易日；公开接口最多20日。` : ''}${data ? `<br><span class="kw-muted">读取时间 ${esc(data.readAt)} · ${data.asset_type === 'etf' ? 'ETF，三位价格精度' : '股票，两位价格精度'}</span>` : ''}`;
    $('.kw-availability').textContent = enabled.filter(n => current.continuous < n).map(n => `MA${n}需要${n}根，当前末段有效历史${current.continuous}根`).join('；');
    $('.kw-message').textContent = !data ? '仅在选择标的后读取行情。' : [
      data.errors[minute ? 'minute' : 'daily'] ? `官方读取失败：${data.errors[minute ? 'minute' : 'daily']}` : '',
      !current.validCount ? '当前没有可绘制的完整K，本地范围读取不会自动补拉历史。' : '',
      minute && !calendar.length ? '日K日历不可用，均线在跨日处断开。' : '',
      '分钟价按官方返回口径绘制，不二次复权；缺口中断MA，未完成区间不画蜡烛。60m MA233在20日窗口内历史不足。',
    ].filter(Boolean).join(' ');
    $('.kw-anchor').innerHTML = model.event ? `${esc(anchor.message)} <button data-action="focus" ${anchor.index < 0 ? 'disabled' : ''}>定位信号</button>` : '';
  }
  async function load(item, options = {}) {
    const id = ++revision, same = model.selected?.symbol === item.symbol;
    if (model.event && item.symbol !== model.eventSymbol) model.event = null;
    model.selected = item; model.loading = true; model.error = ''; model.matches = []; $('.kw-results').textContent = '';
    if (!same) model.data = null;
    writeUrl(); update(!same);
    try {
      const data = await reader.read(item.symbol, { ...options, now: now() });
      if (!disposed && id === revision && data) {
        if (same && model.data) {
          if (data.errors.minute) data.sessions = model.data.sessions;
          if (data.errors.daily) data.daily = model.data.daily;
        }
        model.data = data;
        if (Object.keys(data.errors).length) model.error = Object.entries(data.errors).map(([kind, message]) => `${kind === 'minute' ? '分钟' : '日K'}读取失败：${message}`).join('；') + (same ? '；保留上次有效输入' : '');
      }
    }
    catch (error) { if (!disposed && id === revision) model.error = `行情读取失败：${error.message}`; }
    finally { if (!disposed && id === revision) { model.loading = false; update(true); } }
  }
  async function search(query, auto = false) {
    const id = ++searchRevision; model.error = ''; controls();
    try {
      const matches = await searchInstruments(ctx.api, query);
      if (disposed || id !== searchRevision) return;
      model.matches = matches;
      const exact = matches.find(r => r.symbol === fullSymbol(query));
      if (auto && exact) { await load(exact); return; }
      $('.kw-results').innerHTML = matches.map(r => `<button data-symbol="${esc(r.symbol)}">${esc(r.name)} ${esc(r.symbol)} · ${r.asset_type === 'etf' ? 'ETF' : '股票'}</button>`).join('');
      if (!matches.length) model.error = '未找到股票或ETF，请输入名称或完整.SH/.SZ/.BJ代码';
    } catch (error) { if (!disposed && id === searchRevision) model.error = `搜索失败：${error.message}`; }
    if (!disposed && id === searchRevision) controls();
  }
  function applyVisible(value) {
    const n = Number(value);
    if (value === '' || !Number.isInteger(n) || (n !== 0 && n < 10) || n > 10000) {
      model.error = '显示根数为10–10000，或选择全部'; controls(); return;
    }
    model.visible = n; model.error = ''; save(); update(); plot?.range(n);
  }
  const submit = e => { e.preventDefault(); void search($('[aria-label="股票或ETF搜索"]').value, true); };
  const click = e => {
    const symbol = e.target.closest('[data-symbol]')?.dataset.symbol;
    if (symbol) { void load(model.matches.find(r => r.symbol === symbol)); return; }
    const remove = e.target.closest('[data-ma-remove]');
    if (remove) { model.mas.splice(Number(remove.dataset.maRemove), 1); save(); update(); return; }
    const action = e.target.closest('[data-action]')?.dataset.action;
    if (action === 'refresh') void load(model.selected, { force: true });
    else if (action === 'tail') void load(model.selected, { force: true, tail: true });
    else if (action === 'focus') plot?.focus();
    else if (action === 'official') ctx.ui?.openStockPreview({ symbol: model.selected.symbol, name: model.selected.name });
    else if (action === 'range') applyVisible($('[aria-label="初始显示根数"]').value === '0' && !$('[aria-label="自定义可见根数"]').value ? '0' : $('[aria-label="自定义可见根数"]').value);
    else if (action === 'add-ma') {
      const n = Number($('[aria-label="新增MA周期"]').value);
      if (!Number.isInteger(n) || n < 1 || n > 2000 || model.mas.some(m => m.period === n) || model.mas.length >= 12) model.error = '周期需为1–2000的不同整数，最多12条MA';
      else { model.mas.push({ period: n, enabled: true }); model.error = ''; save(); }
      update();
    }
  };
  const change = e => {
    const target = e.target;
    if (target.matches('[aria-label="K线级别"]')) { model.period = target.value; if (model.selected) writeUrl(); update(true); return; }
    if (target.matches('[aria-label="初始显示根数"], [aria-label="自定义可见根数"]')) {
      if (target.value === 'custom') { $('[aria-label="自定义可见根数"]').focus(); return; }
      applyVisible(target.value); return;
    }
    if (target.dataset.maToggle !== undefined) model.mas[Number(target.dataset.maToggle)].enabled = target.checked;
    else if (target.dataset.maPeriod !== undefined) {
      const i = Number(target.dataset.maPeriod), n = Number(target.value);
      if (!Number.isInteger(n) || n < 1 || n > 2000 || model.mas.some((m,j) => j !== i && m.period === n)) { model.error = 'MA周期需为1–2000的不同整数'; controls(); return; }
      model.mas[i].period = n;
    } else return;
    model.error = ''; save(); update();
  };
  $('.kw-search').addEventListener('submit', submit); root.addEventListener('click', click); root.addEventListener('change', change);
  update();
  if (link.symbol) { $('[aria-label="股票或ETF搜索"]').value = link.symbol; void search(link.symbol, true); }
  return () => { disposed = true; revision++; searchRevision++; reader.dispose(); plot?.dispose(); root.remove(); };
}
