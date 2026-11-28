import { mountFocusControls } from './focus_controls.mjs';
import { createWorkbenchChart } from './chart.mjs';
import { AVERAGE_COLORS, chartModel, beijing } from './chart_model.mjs';
import { activationAccess, activationResults, matrixSignal, resultsMarkup } from './activation_results.mjs';
import { industryGroups, industryNodes, industryPath } from './industry_groups.mjs';
import { escapeHtml as esc, icon, price, quantity, signed } from './presentation.mjs';
import { filterRows, savedScopes, scopeButtons, scopedRows, short, stockRows } from './stock_matrix.mjs';
import { chartDiagnosis, paintRuntime } from './status_view.mjs';
import { CSS, WORKBENCH_CSS } from './styles.mjs';
import { createWorkbenchIO, currentFocus, deepLink, normalizedTime, PERIOD_LABEL, statusText, TIMEFRAMES } from './workbench_data.mjs';
import { LAYOUT } from './workbench_layout.mjs';

export function mountWorkbench(el, ctx, { makeChart = createWorkbenchChart, interval = 5000, ioOptions } = {}) {
  const doc = el.ownerDocument, win = doc.defaultView, node = doc.createElement('main');
  node.className = 'mr mr-workbench';
  node.innerHTML = `<style>${CSS}${WORKBENCH_CSS}</style>${LAYOUT}`;
  el.append(node);
  const $ = role => node.querySelector(`[data-role="${role}"]`);
  const action = name => node.querySelector(`[data-act="${name}"]`);
  const io = createWorkbenchIO(ctx.api, ioOptions);
  let view = null, lastRaw = null, lastFocus = null, board = '';
  let runtime = null, statusError = '', viewError = '', reason = '';
  let access = activationAccess(null, null), resultPeriod = '15m', resultToday = false, expanded = false, results = [];
  const parts = new Map(), hiddenAverages = new Set(), collapsedMatrix = new Set(), collapsedResults = new Set();
  const railClosed = new Set(), eventDetails = new Set();
  let catalog, nodes = new Map();
  let selection = null, chart = null, chartSignature = null, filtered = [];
  let loadingChart = false, refreshPending = false, stopped = false, chartGeneration = 0;
  let archiveOffset = 0, archiveRows = [], managerTrigger;
  const focus = mountFocusControls($('focus'), {
    onApply: args => edit('save', args),
    onPreview: args => edit('preview', args),
    onEstimate: args => edit('estimate', args),
    onRefresh: args => edit('refresh', args),
    onLookup: args => io.submit('members', args),
  });

  function decorateFocus(data) {
    const source = Object.values(data.catalog_source_ms || {}).filter(Number.isFinite);
    const at = source.length ? beijing(Math.min(...source) / 1000).slice(0, 16) : '';
    return { ...data, catalog_status: [
      data.catalog_error ? statusText(data.catalog_error) : null,
      data.catalog_stale && at ? '目录为旧快照' : null,
      at ? `目录接口时间 ${at}` : null,
    ].filter(Boolean).join(' · ') };
  }
  function updateFocus(data) {
    if (data !== lastFocus && currentFocus(data, lastFocus)) {
      lastFocus = data;
      focus.update(decorateFocus(data));
    }
  }
  async function edit(name, args) {
    try {
      const result = await io.submit('edit', { ...args, action: name });
      if (stopped || !result) return null;
      if (name === 'preview' || name === 'estimate') return result;
      if (currentFocus(result.focus, lastFocus)) lastFocus = result.focus;
      if (view && lastFocus.revision >= view.focus.revision) {
        const memberKeys = new Set(Object.keys(lastFocus.members));
        view = { ...view, focus: lastFocus, rows: view.rows.filter(row => memberKeys.has(row.symbol)) };
        renderTable();
        paintRuntime(node, runtime, view, { statusError, viewError });
      }
      void refresh(true);
      return decorateFocus(lastFocus);
    } catch (error) {
      if (error.focus) updateFocus(error.focus);
      throw error;
    }
  }
  function repaint(role, markup, wrap = $(role)) {
    const el = $(role);
    if (parts.get(role) === markup) return;
    const scroll = [wrap.scrollLeft, wrap.scrollTop];
    const focusKey = el.contains(doc.activeElement) ? Object.entries(doc.activeElement.dataset) : [];
    el.innerHTML = markup;
    parts.set(role, markup);
    [wrap.scrollLeft, wrap.scrollTop] = scroll;
    if (focusKey.length) [...el.querySelectorAll('button')].find(button =>
      focusKey.every(([key, value]) => button.dataset[key] === value))?.focus({ preventScroll: true });
  }
  const search = () => $('search').value.trim().toLowerCase();
  const resultContext = () => JSON.stringify([board, search(), resultPeriod,
    TIMEFRAMES.indexOf(resultPeriod) < 4 && resultToday]);

  function renderTable() {
    const rows = view?.rows || [];
    if (catalog !== view?.focus.catalog) {
      catalog = view?.focus.catalog;
      nodes = industryNodes(catalog);
    }
    const paths = view?.industry_paths || {};
    const options = savedScopes(view?.focus, nodes);
    if (!options.some(item => item.key === board)) board = '';
    repaint('tabs', scopeButtons(options, board, $('scope-search').value.trim().toLowerCase(), nodes, railClosed));
    $('filter-period').value = resultPeriod;
    $('matrix').dataset.periodLens = resultPeriod;
    const scope = options.find(item => item.key === board);
    const query = search();
    const scoped = scopedRows(rows, scope, query);
    access = activationAccess(view, runtime, { statusError, viewError });
    const matching = filterRows(scoped, { state: $('filter').value, period: resultPeriod, order: $('order').value, reason }, access);
    const groups = industryGroups(matching, row => row, nodes, paths, scope);
    filtered = groups.flatMap(group => collapsedMatrix.has(group.key) ? [] : group.items);
    const markup = resultsMarkup(activationResults(scoped, access), access, {
      timeframe: resultPeriod, today: resultToday, expanded, scope, scopeLabel: query ? `${scope.name} · 搜索结果` : scope.name,
      nodes, paths, collapsed: collapsedResults, details: eventDetails, eventId: selection?.eventId,
    });
    results = markup.entries;
    $('result-list').dataset.expanded = String(expanded);
    repaint('result-periods', markup.tabs);
    repaint('result-controls', markup.controls);
    repaint('result-list', markup.list);
    $('row-count').textContent = view ? `${quantity(matching.length)} 只股票` : '';
    action('clear-reason').hidden = !reason;
    action('clear-reason').textContent = reason ? `${statusText(reason)} ×` : '';
    const empty = !view ? (viewError ? '观察结果暂不可读' : '等待后台发布观察结果') : rows.length
      ? '没有符合筛选的股票' : '在关注管理中保存板块后显示成分股';
    repaint('stocks', stockRows(groups, collapsedMatrix, selection?.symbol, empty, access), $('table-wrap'));
    renderChartContext();
  }
  function navigation() {
    if (selection?.source !== 'focus') return [];
    if (selection.origin !== 'activation') return filtered;
    if (selection.context !== resultContext()) return [];
    const current = new Map(results.map(entry => [entry.row.symbol, entry]));
    if (!current.get(selection.symbol)?.events.some(event => event.id === selection.resultEventId)) return [];
    return selection.order.map(symbol => current.get(symbol)).filter(Boolean).map(entry => entry.row);
  }
  function renderChartContext() {
    if (!selection) return;
    const list = navigation();
    const index = list.findIndex(item => item.symbol === selection.symbol);
    action('previous').disabled = index < 1;
    action('next').disabled = index < 0 || index === list.length - 1;
    action('preview').disabled = !ctx.ui?.openStockPreview;
    const context = selection.origin === 'activation' ? '激活结果' : '股票矩阵';
    $('nav-context').textContent = selection.source !== 'focus' ? '' : index < 0 ? `不在当前范围 · ${context}` : context;
  }
  function renderChartHeader() {
    if (!selection) return;
    const row = view?.rows.find(item => item.symbol === selection.symbol);
    const legacy = selection.source !== 'focus';
    $('selection').textContent = `${row?.name || selection.name || selection.symbol} ${row || selection.name ? selection.symbol : ''}`.trim();
    const leaf = view?.industry_paths?.[selection.symbol];
    const path = typeof leaf === 'string' ? industryPath(nodes, leaf) : [];
    $('membership').textContent = legacy ? (selection.source === 'archive' ? '冻结 BUY 行情' : '旧版原生15m候选')
      : path.length ? path.map(node => node.name).join(' / ') : Array.isArray(leaf) ? '行业归属待核实' : '未分类';
    for (const [index, button] of [...$('periods').children].entries()) {
      button.setAttribute('aria-pressed', String(button.dataset.period === selection.timeframe));
      button.disabled = legacy && index > 2;
    }
    renderChartContext();
    $('show-levels').disabled = legacy;
    const levels = legacy ? [] : row?.levels?.levels || TIMEFRAMES.map(timeframe => ({ timeframe, status: row?.frames[timeframe]?.status }));
    $('levels').innerHTML = levels.length ? `<div class="mr-levels"><h2>各周期 MA55</h2>
      <p class="mr-meta">${row?.reference ? `参考 ${price(row.reference.price, row.raw_tick)} · ${short(row.reference.at)}` : '等待可比较的完成价'}</p>
      ${levels.map(level => {
        const frame = row?.frames[level.timeframe], signal = frame?.activation;
        const confirmation = access.usable.includes(level.timeframe) ? signal?.events?.at(-1) : null;
        const decision = frame?.result?.latest;
        const position = { PRESSURE: '上方', SUPPORT_REFERENCE: '下方 · 支撑参考', ON: '在线' }[level.side];
        const facts = [
          frame?.expected_end ? `周期结束 ${short(frame.expected_end)}` : null,
          decision?.pattern ? `形态 ${decision.pattern}` : null, decision?.veto ? `限制 ${decision.veto}` : null,
          frame?.basis, decision?.rule_version,
        ].filter(Boolean).join(' · ');
        const review = signal?.review_pending ? '输入变更待复核' : signal?.invalidated ? '复核后有历史确认失效' : '';
        return `<div><strong>${PERIOD_LABEL[level.timeframe]}</strong><span>${price(level.ma55, row?.raw_tick)}</span>
          <small title="${esc(facts)}">${esc(position ? `${position} ${signed(level.distance_pct)}` : statusText(level.status === 'READY' ? 'WAITING_INPUT' : level.status))}</small>
          ${confirmation || level.pressure_test || review ? `<small>${esc([
            confirmation ? `确认 ${short(confirmation.at)} · ${confirmation.mode}` : null,
            level.pressure_test ? '反压观察' : null, review,
          ].filter(Boolean).join(' · '))}</small>` : ''}</div>`;
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
      $('chart').hidden = true; chartReadout(null);
      action('locate').hidden = true;
      $('anchor').textContent = '';
      $('stock-diagnostic').replaceChildren();
      $('chart-state').textContent = '正在读取已完成 K 线…';
    }
    try {
      const { symbol, timeframe, source, anchor, eventId } = selected;
      const data = await io.submit('chart', { symbol, timeframe, source, anchor, eventId });
      if (stopped || !data || generation !== chartGeneration) return;
      if (data.symbol !== selected.symbol || data.timeframe !== selected.timeframe) throw Error('图表回执与所选标的不一致');
      if (data.name) selected.name = data.name;
      const model = chartModel(data, selected);
      const anchorMissing = anchor && model.anchor < 0;
      if (data.event_verified === false) model.anchor = -1;
      const basis = data.bars[0]?.price_basis === 'QFQ' ? '前复权' : '原价';
      const reason = data.status && data.status !== 'READY' ? statusText(data.status) : '';
      $('chart-state').textContent = model.count ? [
        PERIOD_LABEL[selected.timeframe], basis, `${model.count} 根完成 K`, short(data.asof),
        reason, anchorMissing ? '所选确认时刻缺少已保存 K 线' : '',
        data.event_invalidated ? '所选事件经复核已失效' : data.event_verified === false ? '所选事件待核实' : '',
      ].filter(Boolean).join(' · ') : reason || '等待完成 K 线';
      const awaitingView = source === 'focus' && !access.usable.includes(timeframe);
      const published = source === 'focus' && view?.rows.find(row => row.symbol === symbol)?.frames[timeframe];
      const pending = awaitingView ? 'WAITING_FOR_REVIEW' : published?.activation?.state === 'PENDING' ? published.status : null;
      $('stock-diagnostic').innerHTML = chartDiagnosis(data.diagnostic || (pending ? {
        target: access.targets[timeframe] || data.expected_end, reason: pending,
        source: TIMEFRAMES.indexOf(timeframe) < 4 ? '15m' : 'daily',
      } : null));
      $('anchor').textContent = model.anchor < 0 ? '' : `${eventId ? '所选确认' : '参考时刻'} ${normalizedTime(anchor)}`;
      if (model.count) {
        $('chart').hidden = false;
        if (!chart) {
          chart = makeChart($('chart'), bar => chartReadout(bar, data.tick));
          for (const period of hiddenAverages) chart.setAverageVisible(period, false);
        }
        chart.update(model, reset);
      } else if (chart) {
        chart.dispose(); chart = null; $('chart').hidden = true; chartReadout(null);
      }
      action('locate').hidden = model.anchor < 0 || data.event_verified === false;
      renderChartHeader();
    } catch (error) {
      if (!stopped && generation === chartGeneration) $('chart-state').textContent = error.message;
    } finally {
      if (generation === chartGeneration) loadingChart = false;
    }
  }
  function chartReadout(bar, tick) {
    $('readout').textContent = bar ? `${short(bar.timestamp)}　收 ${price(bar.close, tick)}　成交量 ${quantity(bar.volume)} 股` : '';
    for (const entry of $('ma-legend').children) {
      entry.lastElementChild.textContent = price(bar?.volume > 0 ? bar[`ma${entry.dataset.ma}`] : null, tick);
      entry.title = bar && !Number.isFinite(bar[`ma${entry.dataset.ma}`]) ? `需 ${entry.dataset.ma} 根完整连续 K 线` : '';
    }
  }
  function resultSignature() {
    const row = view?.rows.find(item => item.symbol === selection.symbol);
    return JSON.stringify([row?.frames[selection.timeframe], row?.levels, row?.reference]);
  }
  function open(next, scroll = true) {
    if (loadingChart && ['symbol', 'source', 'timeframe', 'eventId', 'anchor'].every(key => selection?.[key] === next[key])) {
      Object.assign(selection, next);
      renderTable();
      return;
    }
    selection = next;
    chartSignature = next.source === 'focus' ? resultSignature() : null;
    $('chart-panel').hidden = false;
    renderTable(); renderChartHeader(); void loadChart(true);
    if (scroll) $('chart-panel').scrollIntoView?.({ block: 'nearest' });
  }
  function openRow(symbol, timeframe, followPeriod = !timeframe) {
    const row = view?.rows.find(item => item.symbol === symbol);
    if (!row) return;
    timeframe ||= resultPeriod;
    const candidate = matrixSignal(row, timeframe, access);
    open({ symbol, timeframe, source: 'focus', origin: 'matrix', followPeriod,
      anchor: candidate?.at || null, eventId: candidate?.id || null });
  }
  function openResult(entry, event, order = results.map(item => item.row.symbol)) {
    open({ symbol: entry.row.symbol, timeframe: resultPeriod, source: 'focus', origin: 'activation',
      context: resultContext(), order, resultEventId: event.id, anchor: event.at, eventId: event.id });
  }
  function selectPeriod(period) {
    if (period === resultPeriod || !TIMEFRAMES.includes(period)) return;
    resultPeriod = period; reason = '';
    renderTable();
    if (selection?.origin === 'matrix' && selection.followPeriod && filtered.some(row => row.symbol === selection.symbol)) {
      openRow(selection.symbol);
    }
  }

  async function refresh(force = false) {
    if (refreshPending || stopped) return;
    refreshPending = true;
    const before = view, beforeError = viewError;
    try {
      try {
        const status = await io.runtime();
        if (status) runtime = status;
        statusError = !status && runtime ? '后台状态暂不可读' : '';
      } catch (error) { statusError = statusText(error.message); }
      if (stopped) return;
      if (!view || force || viewError || (runtime?.result_id && runtime.result_id !== view.result_id)) {
        try {
          const raw = await io.view();
          if (stopped) return;
          if (!raw && view) throw Error('观察结果暂不可读');
          if (raw && raw !== lastRaw) {
            const incoming = JSON.parse(raw);
            if ((!runtime?.result_id || incoming.result_id === runtime.result_id)
                && (!lastFocus || incoming.focus.revision >= lastFocus.revision)) {
              updateFocus(incoming.focus);
              lastRaw = raw; view = { ...incoming, focus: lastFocus };
            }
          }
          viewError = '';
        } catch (error) { viewError = statusText(error.message); }
      }
      const market = runtime?.market_day || view?.summary.market_day;
      $('market').textContent = market ? `交易日 ${market}` : '';
      paintRuntime(node, runtime, view, { statusError, viewError });
      const nextAccess = activationAccess(view, runtime, { statusError, viewError });
      if (before !== view || beforeError !== viewError || JSON.stringify(access) !== JSON.stringify(nextAccess)) renderTable();
      if (view && selection?.source === 'focus' && !loadingChart) {
        const signature = resultSignature();
        if (chartSignature !== signature) {
          chartSignature = signature;
          if (view.rows.some(row => row.symbol === selection.symbol)) void loadChart(false);
        }
      }
    } finally { refreshPending = false; }
  }
  async function history(offset) {
    $('archive-list').textContent = '正在读取冻结历史…';
    try {
      const result = await io.submit('history', { offset });
      if (!result || stopped) return;
      archiveOffset = offset; archiveRows = result.rows || [];
      $('archive-list').innerHTML = archiveRows.length ? `<table class="mr-members"><thead><tr><th>股票</th>
        <th>代码</th><th>确认时间</th><th>模式</th></tr></thead><tbody>${archiveRows.map((row, i) => {
        const symbol = esc(row.symbol), name = row.name && row.name !== row.symbol ? esc(row.name) : '';
        const entry = `<button class="mr-stock-name" data-old="${i}">${name || symbol}</button>`;
        return `<tr><td>${name ? entry : '—'}</td><td class="mr-code">${name ? symbol : entry}</td>
          <td class="mr-code">${esc(short(row.confirmed_at))}</td><td>${esc(row.mode || '历史')}</td></tr>`;
      }).join('')}</tbody></table>` : '暂无冻结记录';
      $('history-page').textContent = archiveRows.length ? `${offset + 1}–${offset + archiveRows.length} / ${quantity(result.total)}` : '';
      action('history-prev').disabled = offset === 0;
      action('history-next').disabled = offset + archiveRows.length >= result.total;
    } catch (error) { if (!stopped) $('archive-list').textContent = error.message; }
  }
  function openManager(button, inspect) {
    const dialog = $('manager');
    if (!dialog.open) {
      managerTrigger = button;
      dialog.showModal();
      dialog.querySelector('[data-role="query"]:enabled')?.focus();
    }
    if (inspect) void focus.inspect(inspect);
  }
  $('search').addEventListener('input', renderTable);
  $('scope-search').addEventListener('input', renderTable);
  $('filter').addEventListener('change', () => { reason = ''; renderTable(); });
  $('filter-period').addEventListener('change', () => selectPeriod($('filter-period').value));
  $('order').addEventListener('change', renderTable);
  $('show-levels').addEventListener('change', renderChartHeader);
  $('archive').addEventListener('toggle', () => { if ($('archive').open) void history(archiveOffset); });
  $('manager').addEventListener('close', () => {
    (managerTrigger?.isConnected ? managerTrigger : action('manage')).focus({ preventScroll: true });
  });
  node.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    const { board: sector, stock, tf, period, act, old, inspect, resultPeriod: chosen,
      resultWindow, signal, ma, railGroup, matrixGroup, activationGroup, resultDetail, blockPeriod, blockReason } = button.dataset;
    if (sector !== undefined) { board = sector; renderTable(); }
    const toggle = (items, key) => { if (!items.delete(key)) items.add(key); renderTable(); };
    if (railGroup) toggle(railClosed, railGroup);
    if (matrixGroup) toggle(collapsedMatrix, matrixGroup);
    if (activationGroup) toggle(collapsedResults, activationGroup);
    if (resultDetail) toggle(eventDetails, resultDetail);
    if (blockReason && access.usable.includes(blockPeriod)) {
      board = ''; resultPeriod = blockPeriod; reason = blockReason;
      $('search').value = ''; $('filter').value = 'all'; collapsedMatrix.clear();
      renderTable();
      $('table-wrap').scrollIntoView?.({ block: 'start' });
      $('table-wrap').focus({ preventScroll: true });
    }
    if (act === 'clear-reason') { reason = ''; renderTable(); $('filter').focus(); }
    if (inspect || act === 'manage') openManager(button, inspect);
    if (act === 'close-manager') $('manager').close();
    if (act === 'toggle-rail') {
      const expanded = button.getAttribute('aria-expanded') !== 'true';
      $('rail').dataset.expanded = String(expanded);
      button.setAttribute('aria-expanded', String(expanded));
      const label = expanded ? '折叠当前关注' : '展开当前关注';
      button.setAttribute('aria-label', label); button.title = label;
    }
    if (chosen) selectPeriod(chosen);
    if (resultWindow && resultToday !== (resultWindow === 'today')) {
      resultToday = resultWindow === 'today'; expanded = false; renderTable();
    }
    if (act === 'expand-results') { expanded = !expanded; renderTable(); }
    if (signal) {
      const entry = results.find(item => item.events.some(event => event.id === signal));
      const event = entry?.events.find(event => event.id === signal);
      if (event) openResult(entry, event);
    }
    if (ma) {
      const visible = hiddenAverages.delete(ma);
      if (!visible) hiddenAverages.add(ma);
      button.setAttribute('aria-pressed', String(visible));
      chart?.setAverageVisible(ma, visible);
    }
    if (stock) openRow(stock, tf);
    if (period && selection && period !== selection.timeframe) {
      const original = selection.original || (selection.anchor ? { timeframe: selection.timeframe,
        anchor: selection.anchor, eventId: selection.eventId } : null);
      const same = original?.timeframe === period;
      open({ ...selection, timeframe: period, followPeriod: false, original,
        anchor: same ? original.anchor : null, eventId: same ? original.eventId : null }, false);
    }
    if (act === 'refresh') { void refresh(); if (selection && !loadingChart) void loadChart(false); }
    if (act === 'locate') chart?.focus();
    if (act === 'preview' && selection) ctx.ui?.openStockPreview({
      symbol: selection.symbol, name: selection.name || view?.rows.find(row => row.symbol === selection.symbol)?.name,
      navList: navigation().map(({ symbol, name }) => ({ symbol, name })),
    });
    if (act === 'previous' || act === 'next') {
      const list = navigation();
      const index = list.findIndex(item => item.symbol === selection?.symbol);
      const next = index < 0 ? null : list[index + (act === 'next' ? 1 : -1)];
      if (next && selection.origin === 'activation') {
        const entry = results.find(item => item.row.symbol === next.symbol);
        openResult(entry, entry.events.at(-1), selection.order);
      } else if (next) openRow(next.symbol, selection.timeframe, selection.followPeriod);
    }
    if (old !== undefined && archiveRows[Number(old)]) {
      const row = archiveRows[Number(old)];
      open({ symbol: row.symbol, name: row.name, timeframe: '15m', source: 'archive',
        anchor: normalizedTime(row.confirmed_at), eventId: row.event_id });
    }
    if (act === 'history-prev' || act === 'history-next') void history(Math.max(0, archiveOffset + (act === 'history-next' ? 50 : -50)));
  });
  $('periods').innerHTML = TIMEFRAMES.map(tf => `<button data-period="${tf}">${PERIOD_LABEL[tf]}</button>`).join('');
  $('ma-legend').innerHTML = Object.entries(AVERAGE_COLORS).map(([period, color]) =>
    `<button type="button" data-ma="${period}" aria-label="MA${period}" aria-pressed="true" style="--ma-color:${color}">MA${period} <span>—</span></button>`).join('');
  paintRuntime(node, null, null);
  renderTable();
  const linked = deepLink(win.location.search);
  if (linked) open(linked, false);
  void refresh();
  const timer = win.setInterval(() => { if (!doc.hidden) void refresh(); }, interval);
  return () => { stopped = true; win.clearInterval(timer); io.dispose(); chart?.dispose(); focus.dispose(); node.remove(); };
}
