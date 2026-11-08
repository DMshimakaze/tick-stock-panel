import { END_MINUTES, beijingNow, endStamp, wallMillis, FINALITY_MS } from './aggregate.mjs';
import { loadCatalog, loadInstrument, refreshInstruments } from './host_data.mjs';
import { consumeBars, DerivedCache } from './state.mjs';
import { createView, chartBars, PAGE_VERSION } from './presentation.mjs';
import { loadSupplyStatus } from './supply_status.mjs';

export function completedBoundary(now) {
  const day = now.slice(0, 10);
  return END_MINUTES.map(m => endStamp(day, m)).filter(t => wallMillis(t) + FINALITY_MS <= wallMillis(now)).at(-1) || '';
}
export function mountObserver(el, ctx) {
  const api = ctx.api, mountedAt = beijingNow();
  let disposed = false, busy = false, reloadQueued = false, generation = 0, timer, supplyTimer, supplyBusy = false;
  let catalog = { entries: [], groups: [], sessions: [] }, cacheReady = false, lastPoll = '', catalogDay = '';
  let calendarBoundary = '';
  const extras = new Map(), records = new Map();
  const cache = new DerivedCache(el.ownerDocument.defaultView.indexedDB);
  const model = { now: mountedAt, items: [], groups: [], group: 'all', asset: 'all',
    page: 0, total: 0, tab: 'today', historyDate: '', selectedSymbol: '', selectedEvent: '',
    query: '', results: [], error: '', cacheError: '', loading: false, refreshing: false,
    supply: {}, supplyLoading: true, uiAvailable: typeof ctx.ui?.openStockPreview === 'function' };
  const render = () => { if (!disposed) { model.now = beijingNow(); view.render(model); } };
  const scope = () => [...extras.values(), ...catalog.entries.filter(r =>
    !extras.has(r.symbol) && (model.group === 'all' || r.group_ids?.includes(model.group)))];

  async function refreshSupply() {
    if (disposed || supplyBusy) return;
    supplyBusy = true; model.supplyLoading = true; render();
    try {
      const result = await loadSupplyStatus(api, model.supply);
      if (!disposed) model.supply = result;
    } finally { supplyBusy = false; model.supplyLoading = false; render(); }
  }

  async function persist(record) {
    records.delete(record.symbol);
    records.set(record.symbol, record);
    if (records.size > 80) records.delete(records.keys().next().value);
    if (cacheReady && !disposed) {
      try { await cache.put(record); }
      catch (error) { model.cacheError = `派生状态保存失败：${error.message}；本次计算保留在当前页面。`; }
    }
  }
  async function compute(item, origin, version) {
    let previous = records.get(item.symbol);
    if (!previous && cacheReady) previous = await cache.get(item.symbol);
    if (disposed || version !== generation) return null;
    const result = consumeBars(previous, item.aggregated.fifteen, {
      symbol: item.symbol, tick: item.tick, factors: item.factors, dailyFactors: item.dailyFactors, factorReasons: item.reasons,
      sessions: catalog.sessions, seenAt: beijingNow(), origin, liveAfter: mountedAt,
    });
    await persist(result.record);
    return { ...item, record: result.record, chart: chartBars(item.aggregated.fifteen, item.factors, catalog.sessions) };
  }
  async function loadPage(reloadCatalog = false) {
    if (disposed) return;
    if (busy) { generation++; reloadQueued = true; return; }
    busy = true; model.loading = true; model.error = ''; const version = ++generation;
    render();
    try {
      if (reloadCatalog || !catalog.sessions.length) {
        catalog = await loadCatalog(api);
        if (disposed) return;
        model.groups = catalog.groups;
        catalogDay = beijingNow().slice(0, 10);
      }
      const all = scope(); model.total = all.length;
      model.page = Math.min(model.page, Math.max(0, Math.ceil(all.length / 20) - 1));
      const targets = all.slice(model.page * 20, model.page * 20 + 20);
      const existing = new Map(model.items.map(i => [i.symbol, i]));
      model.items = targets.map(r => existing.get(r.symbol) || { ...r });
      render(); let cursor = 0;
      await Promise.all(Array.from({ length: Math.min(3, targets.length) }, async () => {
        while (cursor < targets.length && !disposed && version === generation) {
          const target = targets[cursor++];
          try {
            const item = await loadInstrument(api, target.symbol, beijingNow());
            if (disposed || version !== generation) return;
            const computed = await compute({ ...item, name: item.name || target.name }, 'REPLAY', version);
            if (!computed || disposed || version !== generation) return;
            model.items = model.items.map(i => i.symbol === target.symbol ? computed : i);
            if (!model.selectedSymbol) model.selectedSymbol = target.symbol;
          } catch (error) {
            if (disposed || version !== generation) return;
            model.items = model.items.map(i => i.symbol === target.symbol ? { ...i, error: String(error.message || error) } : i);
          }
          render();
        }
      }));
      lastPoll = '';
    } catch (error) { if (!disposed) model.error = `读取宿主数据失败：${error.message || error}`; }
    finally {
      busy = false; model.loading = false; render();
      if (reloadQueued && !disposed) { reloadQueued = false; void loadPage(); }
    }
  }
  async function poll() {
    if (disposed || busy || el.ownerDocument.visibilityState === 'hidden') return;
    const now = beijingNow(), day = now.slice(0, 10), boundary = completedBoundary(now);
    if (catalogDay !== day) { await loadPage(true); return; }
    if (!catalog.sessions.includes(day) && boundary && calendarBoundary !== boundary) {
      calendarBoundary = boundary;
      const calendar = await api.indexDaily('000001.SH', 120);
      if (disposed) return;
      catalog.sessions = [...new Set(calendar.rows.map(r => r.date))].sort();
    }
    if (!catalog.sessions.includes(day) || !boundary || boundary === lastPoll) return;
    busy = true; model.refreshing = true; lastPoll = boundary; const version = generation; render();
    try {
      const items = model.items.filter(i => i.record && !i.error && i.tick);
      for (let i = 0; i < items.length && !disposed; i += 3) {
        let updated;
        try { updated = await refreshInstruments(api, items.slice(i, i + 3), now); }
        catch (error) {
          updated = items.slice(i, i + 3).map(item => ({ ...item, blocked: String(error.message || error) }));
        }
        if (disposed || version !== generation) return;
        for (const item of updated) {
          let next = item;
          if (!item.blocked) {
            next = await compute(item, 'LIVE', version);
            if (next) {
              // History and recent chart exist only in page memory, never in IndexedDB.
              const previous = model.items.find(r => r.symbol === item.symbol);
              const chart = new Map((previous?.chart || []).map(r => [r.timestamp, r]));
              for (const row of next.chart) chart.set(row.timestamp, row);
              // A daily refresh only rechecks today's ratio; retain prior-day ratios in memory.
              next.factors = { ...previous?.factors, ...next.factors };
              next.chart = chartBars([...chart.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp)).slice(-320), next.factors, catalog.sessions);
            }
          }
          if (next && !disposed) model.items = model.items.map(r => r.symbol === item.symbol ? next : r);
        }
        render();
      }
    } finally {
      busy = false; model.refreshing = false; render();
      if (reloadQueued && !disposed) { reloadQueued = false; void loadPage(); }
    }
  }
  const view = createView(el, {
    async action(action, data) {
      if (action === 'tab') { model.tab = data.tab; render(); }
      else if (action === 'select') { model.selectedSymbol = data.symbol; model.selectedEvent = data.event || ''; render(); }
      else if (action === 'preview') {
        const item = model.items.find(i => i.symbol === data.symbol);
        const opened = ctx.ui?.openStockPreview({ symbol: data.symbol, name: item?.name,
          navList: model.items.map(i => ({ symbol: i.symbol, name: i.name })) });
        if (!opened) { model.error = '宿主未能打开个股详情，请检查完整代码及宿主能力。'; render(); }
      } else if (action === 'reload') await loadPage(true);
      else if (action === 'supply-reload') await refreshSupply();
      else if (action === 'previous' || action === 'next') {
        model.page += action === 'next' ? 1 : -1; model.selectedSymbol = ''; model.selectedEvent = '';
        model.items = [];
        await loadPage();
      } else if (action === 'search') {
        model.query = view.query().trim(); if (!model.query) return;
        try {
          const result = await api.instrumentSearch(model.query, 8, 'stock,etf');
          if (disposed) return;
          model.results = result.results; model.error = result.results.length ? '' : '未找到股票或ETF'; render();
        } catch (error) { if (!disposed) { model.error = String(error.message || error); render(); } }
      } else if (action === 'add') {
        const item = model.results.find(r => r.symbol === data.symbol);
        if (item) { extras.set(item.symbol, item); model.results = []; model.page = 0; await loadPage(); }
      }
    },
    async change(name, value) {
      if (name === 'query') { model.query = value; return; }
      if (name === 'group') { model.group = value; model.page = 0; model.selectedSymbol = ''; model.items = []; await loadPage(); }
      else if (name === 'event') { model.selectedEvent = value; render(); }
      else { model[name] = value; render(); }
    },
  });
  render();
  void refreshSupply();
  supplyTimer = setInterval(() => {
    if (el.ownerDocument.visibilityState !== 'hidden') void refreshSupply();
  }, 60_000);
  void (async () => {
    try { await cache.open(); cacheReady = true; }
    catch (error) { model.cacheError = String(error.message || error); }
    if (disposed) { cache.close(); return; }
    await loadPage(true);
    if (disposed) return;
    void poll().catch(error => { if (!disposed) { model.error = String(error.message || error); render(); } });
    timer = setInterval(() => { void poll().catch(error => { if (!disposed) { model.error = String(error.message || error); render(); } }); }, 60_000);
  })();
  return () => { disposed = true; generation++; clearInterval(timer); clearInterval(supplyTimer); cache.close(); view.destroy(); };
}

export default {
  id: 'lan.ma55', apiVersion: 1, version: PAGE_VERSION,
  routes: [{ id: 'observer', path: '/lan-ma55', mount: mountObserver }],
  navigation: [{ id: 'observer', routeId: 'observer', label: 'MA55 结构观察', icon: 'Activity' }],
};
