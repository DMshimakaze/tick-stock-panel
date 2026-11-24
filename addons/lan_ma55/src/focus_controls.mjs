/** Local taxonomy search and the saved focus draft. */
import { beijing } from './chart_model.mjs';
import { SYMBOL, statusText } from './workbench_data.mjs';

const normalize = value => String(value).normalize('NFKC').trim().toLowerCase();
const INITIAL_DIRECTIONS = ['消费电子', '物理AI', '端侧', '半导体材料', '光刻胶', '光刻机', '国产算力'];
const compact = value => normalize(value).replace(/[\s·/\\_\-]+/g, '');
const codeOf = entry => entry.key.split(':').at(-1);
const identity = entry => [entry.name, entry.key, codeOf(entry), codeOf(entry).replace(/\.TI$/, '')];
const orderedMatch = (field, query) => {
  if (query.length < 2) return false;
  let pos = 0;
  for (const char of field) if (char === query[pos]) pos++;
  return pos === query.length;
};
export const splitTerms = value => [...new Set(String(value).split(/[、,，;；\n]+/).map(t => t.trim()).filter(Boolean))];

export function catalogMatches(catalog, query, kind = 'all') {
  const q = normalize(query), short = compact(query);
  return catalog.filter(e => kind === 'all' || e.key.startsWith(kind + ':'))
    .map(entry => {
      if (!short || identity(entry).some(value => normalize(value) === q)) return { entry, score: 0 };
      const words = [...identity(entry), ...(entry.aliases || []), ...(entry.path || [])].map(compact);
      const score = words.some(word => word === short) ? 1
        : words.some(word => word.includes(short)) ? 2
        : words.some(word => orderedMatch(word, short)) ? 3 : Infinity;
      return { entry, score };
    })
    .filter(item => Number.isFinite(item.score))
    .sort((a, b) => a.score - b.score || a.entry.name.localeCompare(b.entry.name, 'zh-CN') ||
      a.entry.key.localeCompare(b.entry.key))
    .map(item => item.entry);
}

export function resolveTerms(catalog, query, selected = []) {
  const keys = new Set(selected), unresolved = [];
  for (const term of splitTerms(query)) {
    const exact = catalog.filter(e => identity(e).map(normalize).includes(normalize(term)));
    if (exact.length === 1 && exact[0].available !== false) keys.add(exact[0].key);
    else unresolved.push(term);
  }
  return { selection: [...keys], unresolved };
}

const kindLabel = key => key.startsWith('industry:') ? '行业' : '概念';
const at = ms => Number.isFinite(ms) ? beijing(ms / 1000).slice(0, 16) : '';
const emptyPrefs = () => ({ groups: [], aliases: {} });
const PAGE = 12;

export function mountFocusControls(el, { onApply, onPreview, onRefresh, onLookup }) {
  const doc = el.ownerDocument, node = doc.createElement('section');
  node.className = 'mr-panel mr-taxonomy';
  node.innerHTML = `<h2>同花顺板块库</h2>
    <p class="mr-meta" data-role="inventory" aria-live="polite"></p>
    <div class="mr-tools"><input aria-label="搜索同花顺板块" placeholder="板块名称、指数代码或股票代码" data-role="query">
      <select aria-label="板块分类" data-role="kind"><option value="all">全部分类</option><option value="industry">行业</option><option value="cn_concept">概念</option></select>
      <button data-role="add">添加精确匹配</button><button data-role="refresh">更新完整库</button></div>
    <details><summary>首次关注方向</summary><div class="mr-tools" data-role="directions"></div></details>
    <p class="mr-meta" data-role="match-count"></p>
    <div class="mr-taxonomy-board-list" data-role="matches"></div>
    <div class="mr-tools"><button data-role="prev" hidden>上一页</button><button data-role="next" hidden>下一页</button></div>
    <section class="mr-taxonomy-detail" data-role="detail" hidden>
      <div class="mr-tools"><strong data-role="detail-title"></strong><button data-role="close">收起</button></div>
      <p class="mr-meta" data-role="detail-source"></p><div class="mr-tools" data-role="detail-rows"></div>
      <div class="mr-tools"><button data-role="detail-prev" hidden>上一页</button><button data-role="detail-next" hidden>下一页</button></div>
      <div class="mr-tools" data-role="alias"><input data-role="alias-input" aria-label="自定义别名" placeholder="自定义别名，可用顿号分隔">
        <button data-role="alias-save">保存别名到草稿</button></div>
    </section>
    <h2>当前关注</h2><div class="mr-tools" data-role="selected"></div>
    <details data-role="group-panel"><summary>自定义关注组</summary>
      <div data-role="groups"></div>
      <div class="mr-tools"><input data-role="group-name" aria-label="自定义组名" maxlength="80" placeholder="输入组名">
        <button data-role="group-add">加入草稿中的板块</button>
        <button data-role="group-save">保存组到草稿</button><button data-role="group-reset">清空编辑</button></div>
      <div class="mr-tools" data-role="group-entries"></div></details>
    <p class="mr-meta" data-role="summary" aria-live="polite"></p><p class="mr-error" data-role="error" role="status"></p>
    <div class="mr-tools"><button data-role="preview">预览变更</button><button data-role="apply">保存关注</button>
      <button data-role="reset">撤销草稿</button></div>`;
  el.append(node);
  const $ = role => node.querySelector(`[data-role="${role}"]`), input = $('query');
  let view, base = [], draft = [], saved = emptyPrefs(), prefs = emptyPrefs(), revision, run;
  let busy = false, disposed = false, page = 0, error = '', delta, detail, lookupId = 0;
  let groupId = null, groupBoards = [];
  const dirty = () => JSON.stringify([base, saved]) !== JSON.stringify([draft, prefs]);
  const conflict = () => dirty() && (revision !== view?.revision || run !== (view?.taxonomy?.run ?? null));
  const entries = () => new Map([...(view?.sectors || []), ...(view?.catalog || [])]
    .map(e => [e.key, { ...e, aliases: prefs.aliases[e.key] || [] }]));
  function button(text, action, { label = text, disabled = false } = {}) {
    const b = doc.createElement('button');
    b.type = 'button'; b.textContent = text; b.disabled = disabled;
    b.setAttribute('aria-label', label); b.addEventListener('click', action);
    return b;
  }
  function changed() { delta = null; error = ''; render(); }
  function choose(key) {
    if (!draft.includes(key)) { draft = [...draft, key]; changed(); }
  }
  function editGroup(group) {
    groupId = group?.id || null;
    groupBoards = [...(group?.boards || [])];
    $('group-name').value = group?.name || '';
    render();
  }
  function render() {
    if (disposed) return;
    const catalog = entries(), selected = $('selected'), matches = $('matches'), t = view?.taxonomy;
    const progress = t?.progress?.board_total ?
      `建库 ${t.progress.board_ready}/${t.progress.board_total} 板块 · ${t.progress.stock_seen}/${t.progress.stock_total} 反查` : '';
    $('inventory').textContent = [t?.complete ? `完整快照 ${at(t.observed_ms)} · ${t.stock_total} 只股票` : '等待完整成分库',
      progress, view?.catalog_status, t?.queued ? '已安排更新' : '', '层级未核实'].filter(Boolean).join(' · ');
    selected.replaceChildren();
    for (const key of draft) {
      const entry = catalog.get(key), snapshot = view?.sectors.find(s => s.key === key);
      const count = snapshot?.symbols.length ?? entry?.count;
      const stale = entry?.removed || view?.sector_errors?.[key] ||
        (snapshot?.observed_ms && at(snapshot.observed_ms).slice(0, 10) !== view?.market_day);
      selected.append(button(`${entry?.name || key}${Number.isInteger(count) ? ` · ${count}只` : ''}${stale ? ' · 旧快照' : ''} ×`,
        () => { draft = draft.filter(k => k !== key); changed(); },
        { label: `移除 ${entry?.name || key}`, disabled: busy }));
    }
    if (!draft.length) selected.textContent = '未选择板块';
    const terms = splitTerms(input.value);
    const query = terms.length === 1 ? terms[0] : '';
    const hits = terms.length > 1 ? [] : catalogMatches([...catalog.values()], query, $('kind').value);
    const shown = hits.slice(page * PAGE, (page + 1) * PAGE);
    $('match-count').textContent = `${hits.length} 个匹配${hits.length > PAGE ? ` · 第 ${page + 1}/${Math.ceil(hits.length / PAGE)} 页` : ''}`;
    matches.replaceChildren();
    for (const entry of shown) {
      const row = doc.createElement('div'), info = doc.createElement('span');
      const brief = entry.removed ? '目录已移除' : entry.available === false ? '等待核实' :
        Number.isInteger(entry.count) ? `${entry.count} 只` : '';
      row.className = 'mr-taxonomy-board';
      info.textContent = [entry.name, codeOf(entry), kindLabel(entry.key), brief].filter(Boolean).join(' · ');
      row.append(info, button('成分', () => void lookup({ board: entry.key }), {
        label: `查看 ${entry.name} 成分`, disabled: !Number.isInteger(entry.count) || busy,
      }), button(draft.includes(entry.key) ? '已选' : '添加', () => choose(entry.key), {
        label: `添加 ${entry.name}`, disabled: draft.includes(entry.key) || entry.available === false || busy,
      }));
      matches.append(row);
    }
    $('prev').hidden = page === 0;
    $('next').hidden = (page + 1) * PAGE >= hits.length;
    $('groups').replaceChildren();
    for (const group of prefs.groups) {
      const row = doc.createElement('div'), name = doc.createElement('span');
      row.className = 'mr-tools';
      name.textContent = `${group.name} · ${group.boards.length} 个板块`;
      row.append(name, button('加入关注', () => {
        const usable = group.boards.filter(k => catalog.get(k)?.available !== false && catalog.has(k));
        draft = [...new Set([...draft, ...usable])]; changed();
        if (usable.length !== group.boards.length) { error = '组内有暂不可用板块'; render(); }
      }), button('编辑', () => editGroup(group), { label: `编辑组 ${group.name}` }),
      button('删除', () => {
        prefs.groups = prefs.groups.filter(g => g.id !== group.id);
        if (groupId === group.id) editGroup(null);
        changed();
      }, { label: `删除组 ${group.name}` }));
      $('groups').append(row);
    }
    $('group-entries').replaceChildren(...groupBoards.map(key => button(`${catalog.get(key)?.name || key} ×`, () => {
      groupBoards = groupBoards.filter(k => k !== key); render();
    })));
    const count = view ? Object.keys(view.members).length : 0;
    $('summary').textContent = view ? `已保存 ${view.sectors.length} 个板块 · 去重 ${count} 只股票${
      delta ? ` · 新增 ${delta.added} · 退出 ${delta.removed} · 保留 ${delta.retained}` : dirty() ? ' · 有未保存草稿' : ''}`
      : '正在读取关注池…';
    $('error').textContent = conflict() ? '关注池或映射代已更新，撤销草稿后可重新编辑。' : error;
    $('apply').disabled = !view || busy || !delta || conflict();
    $('preview').disabled = !view || busy || !dirty() || conflict();
    $('refresh').disabled = !view || busy || Boolean(t?.queued);
    $('reset').disabled = !view || busy || !dirty();
    $('add').textContent = SYMBOL.test(query.toUpperCase()) ? '查股票归属' : '添加精确匹配';
    $('add').disabled = !view || busy || !terms.length;
    input.disabled = !view;
  }
  function update(next, reset = false) {
    if (disposed || !next) return;
    if (!Number.isInteger(next.revision) || !Array.isArray(next.sectors) || !next.members || !Array.isArray(next.catalog)) {
      throw Error('关注池回执无效');
    }
    if (view && next.revision < view.revision) return;
    const clean = !dirty();
    view = next;
    if (clean || reset) {
      base = next.sectors.map(e => e.key);
      draft = [...base];
      saved = { groups: next.groups || [], aliases: next.aliases || {} };
      prefs = { groups: [...saved.groups], aliases: { ...saved.aliases } };
      revision = next.revision;
      run = next.taxonomy?.run ?? null;
      delta = null;
    }
    render();
  }
  function add() {
    const symbol = input.value.trim().toUpperCase();
    if (SYMBOL.test(symbol)) { void lookup({ symbol }); return; }
    const resolved = resolveTerms([...entries().values()], input.value, draft);
    draft = resolved.selection;
    input.value = resolved.unresolved.join('、');
    page = 0;
    changed();
    if (resolved.unresolved.length) { error = '请选择有成分证据的具体板块'; render(); }
  }
  async function lookup(query, offset = 0) {
    const request = ++lookupId;
    const generation = view?.taxonomy?.run;
    if (!generation) return;
    try {
      const next = await onLookup({ ...query, run: generation, offset });
      if (disposed || request !== lookupId || !next || generation !== view.taxonomy?.run) return;
      detail = { query, ...next };
      $('detail').hidden = false;
      $('detail-title').textContent = `${next.board?.name || next.symbol} · ${next.total} ${next.board ? '只' : '个板块'}`;
      $('detail-source').textContent = next.board ?
        `接口时间 ${at(next.board.source_ms) || '供应商未提供'} · 核验 ${at(next.board.observed_ms)} · ${
          next.board.proof === 'BULK_REVERSE_CHECKED' ? '批量反查、按类抽样正查' : '正向全名单'}` : '';
      $('detail-rows').textContent = next.board ? next.rows.join(' · ') :
        next.rows.map(key => entries().get(key)?.name || key).join(' · ');
      $('detail-prev').hidden = offset === 0;
      $('detail-next').hidden = offset + next.rows.length >= next.total;
      $('alias').hidden = !next.board;
      $('alias-input').value = (prefs.aliases[query.board] || []).join('、');
      error = '';
    } catch (e) { if (request === lookupId) error = e.message; }
    render();
  }
  async function submit(action) {
    if (busy) return;
    busy = true; error = ''; render();
    try {
      const args = { expected_revision: revision, run, selection: [...draft], preferences: structuredClone(prefs) };
      if (action === 'preview') delta = await onPreview(args);
      else if (action === 'refresh') update(await onRefresh({ expected_revision: view.revision, run: view.taxonomy?.run ?? null }));
      else update(await onApply(args), true);
    } catch (e) { error = statusText(e.message); }
    finally { busy = false; render(); }
  }
  $('directions').replaceChildren(...INITIAL_DIRECTIONS.map(term => button(term, () => {
    input.value = term; page = 0; render(); input.focus();
  })));
  input.addEventListener('input', () => { page = 0; render(); });
  $('kind').addEventListener('change', () => { page = 0; render(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      if (!$('add').disabled) add();
    }
  });
  $('add').addEventListener('click', add);
  $('prev').addEventListener('click', () => { page--; render(); });
  $('next').addEventListener('click', () => { page++; render(); });
  $('close').addEventListener('click', () => { lookupId++; $('detail').hidden = true; });
  $('detail-prev').addEventListener('click', () => void lookup(detail.query, Math.max(0, detail.offset - 50)));
  $('detail-next').addEventListener('click', () => void lookup(detail.query, detail.offset + 50));
  $('alias-save').addEventListener('click', () => {
    const aliases = splitTerms($('alias-input').value);
    if (aliases.length) prefs.aliases[detail.query.board] = aliases;
    else delete prefs.aliases[detail.query.board];
    changed();
  });
  $('group-add').addEventListener('click', () => { groupBoards = [...new Set([...groupBoards, ...draft])]; render(); });
  $('group-reset').addEventListener('click', () => editGroup(null));
  $('group-save').addEventListener('click', () => {
    const name = $('group-name').value.trim(), id = groupId || doc.defaultView.crypto.randomUUID();
    if (!name || !groupBoards.length || prefs.groups.some(g => g.id !== id && normalize(g.name) === normalize(name))) {
      error = '请填写唯一组名并加入至少一个板块'; render(); return;
    }
    const group = { id, name, boards: [...groupBoards] };
    prefs.groups = groupId ? prefs.groups.map(g => g.id === id ? group : g) : [...prefs.groups, group];
    editGroup(null); changed();
  });
  for (const action of ['preview', 'apply', 'refresh']) $(action).addEventListener('click', () => void submit(action));
  $('reset').addEventListener('click', () => { error = ''; editGroup(null); update(view, true); });
  render();
  return { update, dispose: () => { disposed = true; node.remove(); } };
}
