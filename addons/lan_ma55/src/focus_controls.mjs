/** Local taxonomy search and the saved focus draft. */
import { beijing } from './chart_model.mjs';
import { SYMBOL, statusText } from './workbench_data.mjs';

const normalize = value => String(value).normalize('NFKC').trim().toLowerCase();
const INITIAL_DIRECTIONS = ['端侧AI', '物理AI', '消费电子', '半导体材料', '半导体设备', '光刻胶', '光刻机', '交换机', '超节点'];
const compact = value => normalize(value).replace(/[\s·/\\_\-]+/g, '');
const codeOf = entry => entry.key.split(':').at(-1);
const kindOf = entry => entry.kind || entry.key.split(':').at(-2);
const identity = entry => [entry.name, entry.key, codeOf(entry), codeOf(entry).replace(/\.(TI|DC)$/, '')];
const orderedMatch = (field, query) => {
  if (query.length < 2) return false;
  let pos = 0;
  for (const char of field) if (char === query[pos]) pos++;
  return pos === query.length;
};
export const splitTerms = value => [...new Set(String(value).split(/[、,，;；\n]+/).map(t => t.trim()).filter(Boolean))];

export function catalogMatches(catalog, query, kind = 'all') {
  const q = normalize(query), short = compact(query);
  return catalog.filter(e => kind === 'all' || kindOf(e) === kind)
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

const kindLabel = entry => kindOf(entry) === 'industry' ? '行业' : kindOf(entry) === 'theme' ? '题材' : '概念';
const at = ms => Number.isFinite(ms) ? beijing(ms / 1000).slice(0, 16) : '';
const emptyPrefs = () => ({ groups: [], aliases: {} });
const PAGE = 12;

export function mountFocusControls(el, { onApply, onPreview, onRefresh, onLookup }) {
  const doc = el.ownerDocument, node = doc.createElement('section');
  node.className = 'mr-panel mr-taxonomy';
  node.innerHTML = `<h2>东财板块库</h2>
    <p class="mr-meta" data-role="inventory" aria-live="polite"></p>
    <div class="mr-tools"><input aria-label="搜索东财板块" placeholder="板块、代码、别名或组名" data-role="query">
      <select aria-label="板块分类" data-role="kind"><option value="all">全部</option><option value="industry">行业</option><option value="concept">概念</option><option value="theme">题材</option><option value="groups">我的关注组</option></select>
      <button data-role="add">添加精确匹配</button><button data-role="refresh">更新完整库</button></div>
    <details><summary>首次关注方向</summary><div class="mr-tools" data-role="directions"></div></details>
    <div class="mr-taxonomy-tree" data-role="tree" hidden></div>
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
    <details data-role="migration" hidden><summary>旧同花顺关注归档与迁移</summary>
      <div data-role="migration-rows"></div>
      <label data-role="migration-confirm" hidden><input type="checkbox" data-role="migration-check">确认按这份差集保存新的东财关注</label></details>
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
  let replacements = {};
  const expanded = new Set();
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
  function changed() {
    delta = null; error = ''; $('migration-check').checked = false; render();
  }
  function choose(key) {
    if (!draft.includes(key)) { draft = [...draft, key]; changed(); }
  }
  function editGroup(group) {
    groupId = group?.id || null;
    groupBoards = [...(group?.boards || [])];
    $('group-name').value = group?.name || '';
    render();
  }
  function joinGroup(group, catalog) {
    const usable = group.boards.filter(key => catalog.has(key) && catalog.get(key).available !== false);
    draft = [...new Set([...draft, ...usable])];
    changed();
    if (usable.length !== group.boards.length) { error = '组内有暂不可用板块'; render(); }
  }
  function boardRow(entry) {
    const row = doc.createElement('div'), info = doc.createElement('span');
    const brief = entry.available === false ? '待核实' : Number.isInteger(entry.count) ? `${entry.count} 只` : '';
    row.className = 'mr-taxonomy-board';
    info.textContent = [entry.name, codeOf(entry), kindLabel(entry), brief,
      entry.status === 'STALE' ? '旧快照' : entry.status === 'VERIFIED_EMPTY' ? '核实为空' : ''].filter(Boolean).join(' · ');
    row.append(info, button('成分', () => void lookup({ board: entry.key }), {
      label: `查看 ${entry.name} 成分`, disabled: !Number.isInteger(entry.count) || busy,
    }), button(draft.includes(entry.key) ? '已选' : '添加', () => choose(entry.key), {
      label: `添加 ${entry.name}`, disabled: draft.includes(entry.key) || entry.available === false || busy,
    }));
    return row;
  }
  function renderTree(catalog, visible) {
    const tree = $('tree');
    const scroll = tree.scrollTop;
    const entries = [...catalog.values()].filter(entry => entry.level >= 1 && kindOf(entry) === 'industry');
    tree.hidden = !visible || !entries.length;
    tree.replaceChildren();
    if (tree.hidden) return;
    const children = new Map();
    for (const entry of entries) {
      const parent = entry.parent || null;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(entry);
    }
    function branch(entry) {
      const subs = children.get(entry.key);
      if (!subs?.length) return boardRow(entry);
      const item = doc.createElement('details'), summary = doc.createElement('summary');
      const content = doc.createElement('div');
      summary.textContent = `${entry.name} · ${codeOf(entry)} · ${entry.count} 只`;
      item.append(summary, content);
      item.open = expanded.has(entry.key);
      function fill() {
        if (item.open) {
          expanded.add(entry.key);
          if (!content.hasChildNodes()) content.append(boardRow(entry), ...subs.map(branch));
        } else {
          expanded.delete(entry.key);
          content.replaceChildren();
        }
      }
      if (item.open) fill();
      item.addEventListener('toggle', fill);
      return item;
    }
    tree.append(...(children.get(null) || []).map(branch));
    tree.scrollTop = scroll;
  }
  function renderMigration(catalog) {
    const legacy = view?.legacy, section = $('migration'), rows = $('migration-rows');
    const comparison = new Map((delta?.migration || []).map(row => [row.old_key, row]));
    section.hidden = !(legacy?.sectors.length || legacy?.groups.length || Object.keys(legacy?.aliases || {}).length);
    rows.replaceChildren();
    for (const entry of legacy?.sectors || []) {
      const row = doc.createElement('div'), identity = doc.createElement('span');
      row.className = 'mr-tools';
      identity.textContent = `${entry.name} · ${entry.key} · ${entry.count} 只`;
      row.append(identity);
      if (legacy.pending) {
        const select = doc.createElement('select');
        select.setAttribute('aria-label', `迁移 ${entry.name}`);
        const empty = doc.createElement('option'); empty.textContent = '暂不映射'; empty.value = '';
        select.append(empty);
        for (const key of draft.filter(key => catalog.has(key))) {
          const board = catalog.get(key), option = doc.createElement('option');
          option.value = key; option.textContent = `${board.name} · ${key}`; select.append(option);
        }
        select.value = replacements[entry.key] || '';
        select.addEventListener('change', () => {
          if (select.value) replacements[entry.key] = select.value;
          else delete replacements[entry.key];
          changed();
        });
        row.append(select);
      }
      const diff = comparison.get(entry.key);
      if (diff) {
        const counts = doc.createElement('span');
        counts.textContent = diff.new_key ?
          `新名单 ${diff.new_count} · 共有 ${diff.common} · 旧独有 ${diff.old_only} · 新独有 ${diff.new_only}` : '未匹配';
        row.append(counts);
      }
      rows.append(row);
    }
    for (const group of legacy?.groups || []) {
      const groupText = doc.createElement('p'); groupText.className = 'mr-meta';
      groupText.textContent = `旧组 ${group.name} · ${group.boards.join('、')}`; rows.append(groupText);
    }
    for (const [key, values] of Object.entries(legacy?.aliases || {})) {
      const alias = doc.createElement('p'); alias.className = 'mr-meta';
      alias.textContent = `旧别名 ${key} · ${values.join('、')}`; rows.append(alias);
    }
    $('migration-confirm').hidden = !(legacy?.pending && delta);
  }
  function render() {
    if (disposed) return;
    const catalog = entries(), selected = $('selected'), matches = $('matches'), t = view?.taxonomy;
    const progress = t?.progress?.board_total ? `建库 ${t.progress.board_ready}/${t.progress.board_total} 板块` : '';
    $('inventory').textContent = [t?.complete ? `${t.stale ? '待更新' : '完整'} ${t.market_day || at(t.observed_ms)} · ${t.stock_total} 只股票` : '等待完整成分库',
      t?.hierarchy_status === 'DERIVED_FROM_DC_MEMBERSHIP' ? '东财成分关系重建' : '层级未核实',
      t?.unclassified_count ? `行业未归属 ${t.unclassified_count} 只` : '',
      progress, t?.error ? statusText(t.error) : '', t?.retry_at ? `下次重试 ${at(t.retry_at * 1000)}` : '',
      t?.queued ? '已安排更新' : ''].filter(Boolean).join(' · ');
    selected.replaceChildren();
    for (const key of draft) {
      const entry = catalog.get(key), snapshot = view?.sectors.find(s => s.key === key);
      const count = snapshot?.symbols.length ?? entry?.count;
      const stale = entry?.removed || view?.sector_errors?.[key] ||
        entry?.status === 'STALE' || (snapshot?.data_day && snapshot.data_day < (t?.market_day || snapshot.data_day));
      selected.append(button(`${entry?.name || key}${Number.isInteger(count) ? ` · ${count}只` : ''}${stale ? ' · 旧快照' : ''} ×`,
        () => { draft = draft.filter(k => k !== key); changed(); },
        { label: `移除 ${entry?.name || key}`, disabled: busy }));
    }
    if (!draft.length) selected.textContent = '未选择板块';
    const terms = splitTerms(input.value);
    const query = terms.length === 1 ? terms[0] : '';
    const kind = $('kind').value, treeVisible = !terms.length && ['all', 'industry'].includes(kind);
    renderTree(catalog, treeVisible);
    const groupHits = ['all', 'groups'].includes(kind) && terms.length <= 1 ?
      prefs.groups.filter(group => !query || compact(group.name).includes(compact(query))) : [];
    const hits = terms.length > 1 || kind === 'groups' ? [] : catalogMatches([...catalog.values()], query, kind)
      .filter(entry => !(treeVisible && entry.level >= 1));
    const shown = hits.slice(page * PAGE, (page + 1) * PAGE);
    $('match-count').textContent = `${hits.length + groupHits.length} 个匹配${hits.length > PAGE ? ` · 第 ${page + 1}/${Math.ceil(hits.length / PAGE)} 页` : ''}`;
    matches.replaceChildren();
    for (const group of groupHits) matches.append(button(`关注组 ${group.name}`, () => joinGroup(group, catalog)));
    matches.append(...shown.map(boardRow));
    $('prev').hidden = page === 0;
    $('next').hidden = (page + 1) * PAGE >= hits.length;
    $('groups').replaceChildren();
    for (const group of prefs.groups) {
      const row = doc.createElement('div'), name = doc.createElement('span');
      row.className = 'mr-tools';
      name.textContent = `${group.name} · ${group.boards.length} 个板块`;
      row.append(name, button('加入关注', () => joinGroup(group, catalog)),
      button('编辑', () => editGroup(group), { label: `编辑组 ${group.name}` }),
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
      delta ? ` · 新增 ${delta.added} · 退出 ${delta.removed} · 保留 ${delta.retained}${delta.total != null ? ` · 预览 ${delta.total} 只` : ''}` : dirty() ? ' · 有未保存草稿' : ''}`
      : '正在读取关注池…';
    $('error').textContent = conflict() ? '关注池或映射代已更新，撤销草稿后可重新编辑。' : error;
    renderMigration(catalog);
    $('apply').disabled = !view || busy || !delta || conflict() ||
      (view.legacy?.pending && !$('migration-check').checked);
    $('preview').disabled = !view || busy || (!dirty() && !view.legacy?.pending) || conflict();
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
    const clean = !dirty(), sameGeneration = next.revision === revision && (next.taxonomy?.run ?? null) === run;
    view = next;
    if (clean || reset) {
      base = next.sectors.map(e => e.key);
      draft = [...base];
      saved = { groups: next.groups || [], aliases: next.aliases || {} };
      prefs = { groups: [...saved.groups], aliases: { ...saved.aliases } };
      revision = next.revision;
      run = next.taxonomy?.run ?? null;
      if (reset || !sameGeneration) {
        delta = null;
        replacements = {};
        $('migration-check').checked = false;
      }
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
        [next.board.data_day, next.board.path?.join(' → '), `核验 ${at(next.board.observed_ms)}`].filter(Boolean).join(' · ') :
        next.industry_path?.join(' → ') || '';
      const catalog = entries();
      const fields = next.board ? next.rows.map(row => typeof row === 'string' ? row :
        [row.name, row.symbol, row.reason].filter(Boolean).join(' · ')) :
        next.rows.map(key => {
          const entry = catalog.get(key);
          return entry ? [entry.name, codeOf(entry), kindLabel(entry)].join(' · ') : key;
        });
      $('detail-rows').replaceChildren(...fields.map(value => {
        const entry = doc.createElement('div'); entry.textContent = value; return entry;
      }));
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
      if (view.legacy?.pending) {
        args.migration = Object.fromEntries(Object.entries(replacements).filter(([, key]) => draft.includes(key)));
        args.confirm_migration = $('migration-check').checked;
      }
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
  $('migration-check').addEventListener('change', render);
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
