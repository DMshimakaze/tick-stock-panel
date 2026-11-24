import { mountFocusControls } from './focus_controls.mjs';
import { createWorkbenchChart } from './chart.mjs';
import { chartModel, beijing } from './chart_model.mjs';
import { CSS, WORKBENCH_CSS } from './styles.mjs';
import { createWorkbenchIO, deepLink, normalizedTime, PERIOD_LABEL, statusText, TIMEFRAMES } from './workbench_data.mjs';

const esc = value => String(value ?? '').replace(/[&<>"']/g, char =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const signed = value => Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
const price = (value, tick) => Number.isFinite(value) ? value.toFixed(tick === .001 ? 3 : 2) : '—';
const currentDecision = frame => frame?.status === 'READY' ? frame.result?.latest : null;
const newest = row => TIMEFRAMES.map(tf => [tf, row.frames[tf]?.latest_candidate?.at || ''])
  .sort((a, b) => b[1].localeCompare(a[1]))[0];
const short = value => normalizedTime(value).slice(0, 16) || '—';

function frameLabel(frame) {
  if (!frame || frame.status !== 'READY') {
    return ['INSUFFICIENT_HISTORY', 'MISSING_COMPLETED_BAR', 'WAITING_FOR_COMPLETED_TARGET', 'WAITING_INPUT']
      .includes(frame?.status) ? '待补' : '—';
  }
  const decision = currentDecision(frame);
  if (decision?.event_id) return '候选';
  if (decision?.pressure_test) return '反压';
  return decision?.impulse_recent || decision?.support_test_time ? '观察' : '就绪';
}

export function mountWorkbench(el, ctx, { makeChart = createWorkbenchChart, interval = 5000, ioOptions } = {}) {
  const doc = el.ownerDocument, win = doc.defaultView, node = doc.createElement('main');
  node.className = 'mr mr-workbench';
  node.innerHTML = `<style>${CSS}${WORKBENCH_CSS}</style>
    <header class="mr-header"><div class="mr-heading"><h1>MA55 板块观察</h1><p data-role="market"></p></div>
      <button type="button" data-act="refresh">刷新</button></header>
    <div data-role="focus"></div><p class="mr-error" role="status" data-role="error"></p>
    <div class="mr-coverage" data-role="coverage"></div>
    <section aria-label="关注股票"><div class="mr-tabs mr-sector-tabs" data-role="tabs"></div>
      <div class="mr-tools"><input data-role="search" aria-label="搜索股票" placeholder="名称或代码">
        <label>状态 <select data-role="filter"><option value="all">全部</option><option value="候选">候选</option><option value="观察">观察</option>
        <option value="反压">反压</option><option value="blocked">待补 / 阻塞</option></select></label>
        <label>周期 <select data-role="filter-period"><option value="all">全部</option>${TIMEFRAMES.map(tf => `<option value="${tf}">${PERIOD_LABEL[tf]}</option>`).join('')}</select></label>
        <label>排序 <select data-role="order"><option value="candidate">最近候选</option><option value="symbol">股票代码</option><option value="change">涨跌幅</option></select></label></div>
      <div class="mr-table mr-focus-list" data-role="table-wrap"><table><thead><tr><th>股票</th><th>完成价 / 涨跌</th>
        ${TIMEFRAMES.map(tf => `<th>${PERIOD_LABEL[tf]}</th>`).join('')}<th>最近上方55线</th></tr></thead>
        <tbody data-role="stocks"></tbody></table></div></section>
    <section class="mr-panel mr-chart-panel" data-role="chart-panel" aria-label="所选股票图表" hidden>
      <div class="mr-chart-heading"><div><h2 data-role="selection"></h2><p class="mr-subtitle" data-role="membership"></p></div>
        <div class="mr-controls"><button data-act="previous">上一只</button><button data-act="next">下一只</button><button data-act="preview">官方详情</button></div></div>
      <div class="mr-tools"><div class="mr-controls" data-role="periods"></div>
        <label><input data-role="show-levels" type="checkbox">上方55线</label><button data-act="locate" hidden>定位确认</button></div>
      <p class="mr-meta" data-role="chart-state" role="status"></p><p class="mr-readout" data-role="readout"></p>
      <div class="mr-chart-body"><div class="mr-canvas" data-role="chart" hidden></div><div data-role="levels"></div></div>
    </section>
    <details class="mr-panel" data-role="archive"><summary>冻结 BUY 历史</summary><div data-role="archive-list"></div>
      <div class="mr-pager"><button data-act="history-prev">上一页</button><button data-act="history-next">下一页</button></div></details>
    <details class="mr-diagnostics"><summary>数据诊断</summary><pre data-role="diagnostics"></pre></details>`;
  el.append(node);
  const $ = role => node.querySelector(`[data-role="${role}"]`);
  const action = name => node.querySelector(`[data-act="${name}"]`);
  const io = createWorkbenchIO(ctx.api, ioOptions);
  let view = null, lastRaw = null, lastFocus = null, board = '';
  let selection = null, chart = null, chartSignature = null, filtered = [];
  let loadingChart = false, refreshPending = false, stopped = false, chartGeneration = 0;
  let archiveOffset = 0, archiveRows = [];
  const focus = mountFocusControls($('focus'), {
    onApply: args => edit('save', args),
    onRefresh: args => edit('refresh', args),
  });

  function decorateFocus(data) {
    const source = Object.values(data.catalog_source_ms || {}).filter(Number.isFinite);
    const at = source.length ? beijing(Math.min(...source) / 1000).slice(0, 16) : '';
    return { ...data, sectors: data.sectors.map(sector => ({
      ...sector, checked_day: sector.observed_ms ? beijing(sector.observed_ms / 1000).slice(0, 10) : null,
      source_time: sector.source_ms ? beijing(sector.source_ms / 1000).slice(0, 16) : null,
    })), catalog_status: [
      data.catalog_error ? statusText(data.catalog_error) : null,
      data.catalog_stale && at ? '目录为旧快照' : null,
      at ? `目录源时间 ${at}` : null,
    ].filter(Boolean).join(' · ') };
  }
  function updateFocus(data) {
    const signature = JSON.stringify(data);
    if (signature !== lastFocus) { focus.update(decorateFocus(data)); lastFocus = signature; }
  }
  async function edit(name, args) {
    try {
      const result = await io.submit('edit', { ...args, action: name });
      if (stopped || !result) return null;
      lastFocus = JSON.stringify(result.focus);
      if (view && result.focus.revision >= view.focus.revision) {
        const memberKeys = new Set(Object.keys(result.focus.members));
        view = { ...view, focus: result.focus, rows: view.rows.filter(row => memberKeys.has(row.symbol)) };
        renderTable();
      }
      void refresh();
      return decorateFocus(result.focus);
    } catch (error) {
      if (error.focus) updateFocus(error.focus);
      throw error;
    }
  }
  function renderTable() {
    const rows = view?.rows || [], sectors = view?.focus.sectors || [];
    if (board && !sectors.some(item => item.key === board)) board = '';
    const wanted = $('search').value.trim().toLowerCase(), state = $('filter').value, period = $('filter-period').value;
    const oldTab = doc.activeElement?.dataset.board;
    $('tabs').innerHTML = [{ key: '', name: '全部' }, ...sectors].map(item =>
      `<button type="button" class="${board === item.key ? 'active' : ''}" aria-pressed="${board === item.key}" data-board="${esc(item.key)}">${esc(item.name)}</button>`).join('');
    if (oldTab !== undefined) [...$('tabs').children].find(item => item.dataset.board === oldTab)?.focus({ preventScroll: true });
    filtered = rows.filter(row => (!board || row.sectors.includes(board)) &&
      (!wanted || [row.symbol, row.name].some(value => value.toLowerCase().includes(wanted))) &&
      (state === 'all' || (period === 'all' ? TIMEFRAMES : [period]).some(tf => state === 'blocked'
        ? row.frames[tf]?.status !== 'READY' : frameLabel(row.frames[tf]) === state)));
    const order = $('order').value;
    filtered.sort((a, b) => (order === 'candidate' ? newest(b)[1].localeCompare(newest(a)[1])
      : order === 'change' ? (b.reference?.change_pct ?? -Infinity) - (a.reference?.change_pct ?? -Infinity)
        : 0) || a.symbol.localeCompare(b.symbol));
    const active = doc.activeElement?.dataset;
    $('stocks').innerHTML = filtered.map(row => {
      const top = row.levels?.pressures[0];
      const cells = TIMEFRAMES.map(tf => {
        const frame = row.frames[tf], label = frameLabel(frame);
        const title = frame?.status === 'READY'
          ? [short(frame.expected_end), frame.latest_candidate?.at ? `最近确认 ${short(frame.latest_candidate.at)}` : ''].filter(Boolean).join(' · ')
          : statusText(frame?.status);
        return `<td><button class="mr-frame" data-stock="${row.symbol}" data-tf="${tf}" data-state="${label}" title="${esc(title)}">${label}</button></td>`;
      }).join('');
      const ref = row.reference, change = ref?.change_pct;
      return `<tr class="${selection?.symbol === row.symbol ? 'selected' : ''}"><td><button class="mr-stock-name" data-stock="${row.symbol}">${esc(row.name)}</button>
        <span class="mr-code">${row.symbol}</span></td><td class="mr-price" title="${esc(ref ? `${ref.at} · ${ref.price_basis}` : '等待完成行情')}">
        ${price(ref?.price, row.raw_tick)}<span class="mr-code ${change > 0 ? 'mr-rise' : change < 0 ? 'mr-fall' : ''}">${signed(change)}</span></td>
        ${cells}<td class="mr-price">${top ? `${PERIOD_LABEL[top.timeframe]} · ${price(top.ma55, row.raw_tick)}<span class="mr-code">${signed(top.distance_pct)}</span>` : '—'}</td></tr>`;
    }).join('') || `<tr><td class="mr-empty" colspan="9">${!view ? '正在读取工作台…' : rows.length ? '没有符合筛选的股票' : '保存已核实板块后显示成分股'}</td></tr>`;
    if (active?.stock) [...$('stocks').querySelectorAll('button')].find(item =>
      item.dataset.stock === active.stock && item.dataset.tf === active.tf)?.focus({ preventScroll: true });
  }
  function renderChartHeader() {
    if (!selection) return;
    const row = view?.rows.find(item => item.symbol === selection.symbol);
    const legacy = selection.source !== 'focus';
    $('selection').textContent = `${row?.name || selection.name || selection.symbol} ${row || selection.name ? selection.symbol : ''}`.trim();
    $('membership').textContent = legacy ? (selection.source === 'archive' ? '冻结 BUY 行情' : '旧版原生15m候选')
      : (row?.sectors || []).map(key => view.focus.sectors.find(item => item.key === key)?.name || key).join(' · ');
    $('periods').innerHTML = TIMEFRAMES.map((tf, index) => `<button data-period="${tf}" aria-pressed="${tf === selection.timeframe}" ${legacy && index > 2 ? 'disabled' : ''}>${PERIOD_LABEL[tf]}</button>`).join('');
    const index = filtered.findIndex(item => item.symbol === selection.symbol);
    action('previous').disabled = index < 1;
    action('next').disabled = index < 0 || index === filtered.length - 1;
    action('preview').disabled = !ctx.ui?.openStockPreview;
    $('show-levels').disabled = legacy;
    const levels = legacy ? [] : row?.levels?.levels || TIMEFRAMES.map(timeframe => ({ timeframe, status: row?.frames[timeframe]?.status }));
    $('levels').innerHTML = levels.length ? `<div class="mr-levels"><h2>各周期 MA55</h2>
      <p class="mr-meta">${row?.reference ? `参考 ${price(row.reference.price, row.raw_tick)} · ${short(row.reference.at)}` : '等待可比较的完成价'}</p>
      ${levels.map(level => {
        const frame = row?.frames[level.timeframe], confirmation = frame?.latest_candidate;
        const position = { PRESSURE: '上方', SUPPORT_REFERENCE: '下方 · 支撑参考', ON: '在线' }[level.side];
        return `<div><strong>${PERIOD_LABEL[level.timeframe]}</strong><span>${price(level.ma55, row?.raw_tick)}</span>
          <small>${esc(position ? `${position} ${signed(level.distance_pct)}` : statusText(level.status === 'READY' ? 'WAITING_INPUT' : level.status))}</small>
          ${confirmation || level.pressure_test ? `<small>${confirmation ? `确认 ${short(confirmation.at)} · ${esc(confirmation.mode)}` : ''}${level.pressure_test ? ' · 反压观察' : ''}</small>` : ''}</div>`;
      }).join('')}</div>` : '';
    chart?.setLevels(!legacy && $('show-levels').checked ? (row?.levels?.pressures || [])
      .map(item => ({ price: item.ma55, label: `当前 ${PERIOD_LABEL[item.timeframe]} MA55` })) : []);
  }

  async function loadChart(reset) {
    const selected = selection, generation = ++chartGeneration;
    if (!selected) return;
    loadingChart = true;
    if (reset) {
      chart?.dispose(); chart = null;
      $('chart').hidden = true; $('readout').textContent = '';
      action('locate').hidden = true;
      $('chart-state').textContent = '正在读取已完成 K 线…';
    }
    try {
      const data = await io.submit('chart', selected);
      if (stopped || !data || generation !== chartGeneration) return;
      if (data.symbol !== selected.symbol || data.timeframe !== selected.timeframe) throw Error('图表回执与所选标的不一致');
      if (data.name) selected.name = data.name;
      const model = chartModel(data, selected);
      const basis = data.bars[0]?.price_basis === 'QFQ' ? '前复权' : '原价';
      const reason = data.status && data.status !== 'READY' ? statusText(data.status) : '';
      $('chart-state').textContent = model.count ? [
        PERIOD_LABEL[selected.timeframe], basis, `${model.count} 根完成 K`, short(data.asof),
        reason, selected.anchor && model.anchor < 0 ? '所选确认时刻缺少已保存 K 线' : '',
      ].filter(Boolean).join(' · ') : reason || '等待完成 K 线';
      if (model.count) {
        $('chart').hidden = false;
        if (!chart) chart = makeChart($('chart'), bar => {
          $('readout').textContent = bar ? `${short(bar.timestamp)}　收 ${price(bar.close, data.tick)}　MA55 ${price(bar.ma55, data.tick)}　成交量 ${bar.volume.toLocaleString('zh-CN')} 股` : '';
        });
        chart.update(model, reset);
      } else if (chart) {
        chart.dispose(); chart = null; $('chart').hidden = true; $('readout').textContent = '';
      }
      action('locate').hidden = model.anchor < 0;
      renderChartHeader();
    } catch (error) {
      if (!stopped && generation === chartGeneration) $('chart-state').textContent = error.message;
    } finally {
      if (generation === chartGeneration) loadingChart = false;
    }
  }
  function open(next, scroll = true) {
    selection = next; chartSignature = null;
    $('chart-panel').hidden = false;
    renderTable(); renderChartHeader(); void loadChart(true);
    if (scroll) $('chart-panel').scrollIntoView?.({ block: 'nearest' });
  }
  function openRow(symbol, timeframe, locateCandidate = true) {
    const row = view?.rows.find(item => item.symbol === symbol);
    if (!row) return;
    const [recent, time] = newest(row);
    timeframe ||= time ? recent : '15m';
    const candidate = locateCandidate ? row.frames[timeframe]?.latest_candidate : null;
    open({ symbol, timeframe, source: 'focus', anchor: candidate?.at || null, eventId: candidate?.id || null });
  }

  async function refresh() {
    if (refreshPending || stopped) return;
    refreshPending = true;
    try {
      const raw = await io.view();
      if (stopped) return;
      if (!raw) { $('market').textContent = '等待后台发布工作台'; return; }
      $('error').textContent = '';
      if (raw !== lastRaw) {
        const incoming = JSON.parse(raw);
        if (view && incoming.focus.revision < view.focus.revision) return;
        lastRaw = raw; view = incoming;
        updateFocus(view.focus);
        const summary = view.summary;
        const phase = view.state.phase === 'SCOPE_PAUSED' && view.focus.revision ? '关注池已暂停' : statusText(view.state.phase);
        $('market').textContent = [summary.market_day, `最新完成目标 ${short(summary.target_15m)}`, phase].filter(Boolean).join(' · ');
        $('coverage').innerHTML = [['关注板块', view.focus.sectors.length], ['去重股数', summary.expected],
          ['有周期就绪', summary.ready_symbols], ['结构候选', summary.candidate_symbols], ['待补 / 阻塞', summary.blocked_symbols]]
          .map(([label, count]) => `<span>${label} <strong>${count}</strong></span>`).join('');
        $('diagnostics').textContent = JSON.stringify({ ...view.state, coverage: summary.ready_by_timeframe, rule: view.rule_version }, null, 2);
        renderTable();
      }
      if (selection?.source === 'focus' && !loadingChart) {
        const row = view.rows.find(item => item.symbol === selection.symbol);
        const frame = row?.frames[selection.timeframe];
        const signature = JSON.stringify([frame, row?.levels, row?.reference]);
        if (chartSignature !== signature) {
          const prior = chartSignature;
          chartSignature = signature;
          if (prior !== null || !chart) void loadChart(false);
        }
      }
    } catch (error) { if (!stopped) $('error').textContent = statusText(error.message); }
    finally { refreshPending = false; }
  }
  async function history(offset) {
    $('archive-list').textContent = '正在读取冻结历史…';
    try {
      const result = await io.submit('history', { offset });
      if (!result || stopped) return;
      archiveOffset = offset; archiveRows = result.rows || [];
      $('archive-list').innerHTML = archiveRows.map((row, i) =>
        `<p><button data-old="${i}">${esc(row.name || row.symbol)} · ${row.symbol}</button> ${esc(short(row.confirmed_at))} · ${esc(row.mode || '历史')}</p>`).join('') || '暂无冻结记录';
      action('history-prev').disabled = offset === 0;
      action('history-next').disabled = offset + archiveRows.length >= result.total;
    } catch (error) { if (!stopped) $('archive-list').textContent = error.message; }
  }
  $('search').addEventListener('input', renderTable);
  for (const role of ['filter', 'filter-period', 'order']) $(role).addEventListener('change', renderTable);
  $('show-levels').addEventListener('change', renderChartHeader);
  $('archive').addEventListener('toggle', () => { if ($('archive').open) void history(archiveOffset); });
  node.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    const { board: sector, stock, tf, period, act, old } = button.dataset;
    if (sector !== undefined) { board = sector; renderTable(); }
    if (stock) openRow(stock, tf);
    if (period && period !== selection?.timeframe) {
      if (selection.source === 'focus') openRow(selection.symbol, period, false);
      else open({ ...selection, timeframe: period, anchor: period === '15m' ? selection.anchor : null }, false);
    }
    if (act === 'refresh') { void refresh(); if (selection && !loadingChart) void loadChart(false); }
    if (act === 'locate') chart?.focus();
    if (act === 'preview' && selection) ctx.ui?.openStockPreview({
      symbol: selection.symbol, name: selection.name || view?.rows.find(row => row.symbol === selection.symbol)?.name,
      navList: filtered.map(({ symbol, name }) => ({ symbol, name })),
    });
    if (act === 'previous' || act === 'next') {
      const i = filtered.findIndex(item => item.symbol === selection?.symbol) + (act === 'next' ? 1 : -1);
      if (filtered[i]) openRow(filtered[i].symbol, selection.timeframe);
    }
    if (old !== undefined && archiveRows[Number(old)]) {
      const row = archiveRows[Number(old)];
      open({ symbol: row.symbol, name: row.name, timeframe: '15m', source: 'archive',
        anchor: normalizedTime(row.confirmed_at), eventId: row.event_id });
    }
    if (act === 'history-prev' || act === 'history-next') void history(Math.max(0, archiveOffset + (act === 'history-next' ? 50 : -50)));
  });
  renderTable();
  const linked = deepLink(win.location.search);
  if (linked) open(linked, false);
  void refresh();
  const timer = win.setInterval(() => { if (!doc.hidden) void refresh(); }, interval);
  return () => { stopped = true; win.clearInterval(timer); io.dispose(); chart?.dispose(); focus.dispose(); node.remove(); };
}
