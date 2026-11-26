/** Local taxonomy search and the saved focus draft. */
import { beijing } from './chart_model.mjs';
import { SYMBOL, statusText } from './workbench_data.mjs';
import { codeOf, escapeHtml as esc, icon, kindBadge, kindOf, KINDS, quantity } from './presentation.mjs';
import { DETAIL, paintFocusDetail } from './focus_details.mjs';

const normalize = value => String(value).normalize('NFKC').trim().toLowerCase();
const INITIAL_DIRECTIONS = ['端侧AI', '物理AI', '消费电子', '半导体材料', '半导体设备', '光刻胶', '光刻机', '交换机', '超节点'];
const compact = value => normalize(value).replace(/[\s·/\\_\-]+/g, '');
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

const at = ms => Number.isFinite(ms) ? beijing(ms / 1000).slice(0, 16) : '';
const emptyPrefs = () => ({ groups: [], aliases: {} });
const PAGE = 12;

export function mountFocusControls(el, { onApply, onPreview, onRefresh, onLookup }) {
  const doc = el.ownerDocument, node = doc.createElement('section');
  node.className = 'mr-taxonomy';
  node.innerHTML = `<div class="mr-manager-grid">
    <section class="mr-catalog">
      <header class="mr-panel-head"><h3>东财板块库</h3>
        <button data-role="refresh" class="mr-action" title="更新东财完整分类库">${icon('refresh')}更新分类库</button></header>
      <div class="mr-inventory" data-role="inventory" aria-live="polite"></div>
      <div class="mr-tools"><label class="mr-search">${icon('search')}<input aria-label="搜索东财板块"
        placeholder="板块、代码、别名或组名" data-role="query"></label><button data-role="add">添加精确匹配</button></div>
      <div class="mr-segments" data-role="kind" role="group" aria-label="板块分类">
        ${[['all', '全部'], ['industry', '行业'], ['concept', '概念'], ['theme', '题材'], ['groups', '我的组']]
          .map(([value, label]) => `<button data-kind-value="${value}" aria-pressed="${value === 'all'}">${label}</button>`).join('')}
      </div>
      <details class="mr-directions"><summary>关注方向${icon('chevron')}</summary><div class="mr-tools" data-role="directions"></div></details>
      <div class="mr-taxonomy-tree" data-role="tree" hidden></div>
      <div class="mr-taxonomy-board-list" data-role="matches"></div>
      <div class="mr-pagination"><span class="mr-meta" data-role="match-count"></span>
        <button data-role="prev" class="mr-icon-button" title="分类上一页" aria-label="分类上一页" hidden>${icon('arrowLeft')}</button>
        <button data-role="next" class="mr-icon-button" title="分类下一页" aria-label="分类下一页" hidden>${icon('arrowRight')}</button></div>
    </section>
    ${DETAIL}
    </div>
    <details class="mr-manage-section" data-role="migration" hidden><summary>旧同花顺关注归档与迁移${icon('chevron')}</summary>
      <div data-role="migration-rows"></div>
      <label data-role="migration-confirm" hidden><input type="checkbox" data-role="migration-check">确认按这份差集保存新的东财关注</label></details>
    <details class="mr-manage-section" data-role="group-panel"><summary>自定义关注组${icon('chevron')}</summary>
      <div data-role="groups"></div>
      <div class="mr-tools"><input data-role="group-name" aria-label="自定义组名" maxlength="80" placeholder="输入组名">
        <button data-role="group-add">加入草稿中的板块</button>
        <button data-role="group-save">保存组到草稿</button><button data-role="group-reset">清空编辑</button></div>
      <div class="mr-chips" data-role="group-entries"></div></details>
    <div class="mr-draft">
      <h3>关注草稿</h3><div class="mr-chips" data-role="selected"></div>
      <div class="mr-draft-footer"><div data-role="summary" aria-live="polite"></div>
        <div class="mr-actions"><button data-role="reset">撤销草稿</button><button data-role="preview">预览变更</button>
        <button data-role="apply" class="mr-primary">保存关注</button></div></div>
      <p class="mr-error" data-role="error" role="status"></p>
    </div>`;
  el.append(node);
  const $ = role => node.querySelector(`[data-role="${role}"]`), input = $('query');
  let view, base = [], draft = [], saved = emptyPrefs(), prefs = emptyPrefs(), revision, run;
  let busy = false, disposed = false, page = 0, kind = 'all', error = '', delta, detail, lookupId = 0;
  let groupId = null, groupBoards = [];
  let replacements = {};
  const expanded = new Set();
  const dirty = () => JSON.stringify([base, saved]) !== JSON.stringify([draft, prefs]);
  const conflict = () => dirty() && (revision !== view?.revision || run !== (view?.taxonomy?.run ?? null));
  const entries = () => new Map([...(view?.sectors || []), ...(view?.catalog || [])]
    .map(e => [e.key, { ...e, aliases: prefs.aliases[e.key] || [] }]));
  function button(text, action, { label = text, disabled = false, glyph, key } = {}) {
    const b = doc.createElement('button');
    b.type = 'button'; b.textContent = text; b.disabled = disabled;
    b.setAttribute('aria-label', label);
    if (glyph) {
      b.className = 'mr-icon-button'; b.title = label; b.innerHTML = icon(glyph);
    }
    if (key) b.dataset.focusKey = key;
    b.addEventListener('click', event => { event.preventDefault(); action(); });
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
  function boardRow(entry, expandable = false) {
    const row = doc.createElement(expandable ? 'summary' : 'div');
    const type = kindOf(entry), selected = draft.includes(entry.key);
    const note = entry.available === false ? '待核实' : entry.status === 'STALE' ? '旧快照' : '';
    row.className = 'mr-taxonomy-board';
    if (expandable) row.dataset.focusKey = `tree:${entry.key}`;
    row.innerHTML = `${expandable ? `<span class="mr-tree-chevron">${icon('chevron')}</span>` : ''}
      ${kindBadge(type)}<span class="mr-board-name" title="${esc(entry.name)}"><b>${esc(entry.name)}</b>
      <small>${KINDS[type]?.label || '概念'} <code>${esc(codeOf(entry))}</code>${note ? ` <em>${note}</em>` : ''}</small></span>
      <b class="mr-count">${quantity(entry.count)}</b>`;
    row.append(button('成分', () => void lookup({ board: entry.key }), {
      glyph: 'list', key: `members:${entry.key}`, label: `查看 ${entry.name} 成分`,
      disabled: !Number.isInteger(entry.count) || busy,
    }), button(selected ? '已选' : '添加', () => choose(entry.key), {
      glyph: selected ? 'check' : 'plus', key: `add:${entry.key}`,
      label: `添加 ${entry.name}`, disabled: selected || entry.available === false || busy,
    }));
    return row;
  }
  function renderTree(catalog, visible) {
    const tree = $('tree');
    const scroll = tree.scrollTop;
    const entries = [...catalog.values()].filter(entry => entry.level >= 1 && kindOf(entry) === 'industry');
    tree.hidden = !visible || !entries.length;
    tree.replaceChildren();
    if (tree.hidden) return 0;
    const children = new Map();
    for (const entry of entries) {
      const parent = entry.parent || null;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(entry);
    }
    function branch(entry) {
      const subs = children.get(entry.key);
      if (!subs?.length) return boardRow(entry);
      const item = doc.createElement('details'), summary = boardRow(entry, true);
      const content = doc.createElement('div');
      content.className = 'mr-tree-children';
      item.append(summary, content);
      item.open = expanded.has(entry.key);
      function fill() {
        if (!item.isConnected && !tree.contains(item)) return;
        if (item.open) {
          expanded.add(entry.key);
          if (!content.hasChildNodes()) content.append(...subs.map(branch));
        } else {
          expanded.delete(entry.key);
          content.replaceChildren();
        }
      }
      if (item.open) content.append(...subs.map(branch));
      item.addEventListener('toggle', fill);
      return item;
    }
    tree.append(...(children.get(null) || []).map(branch));
    tree.scrollTop = scroll;
    return entries.length;
  }
  function renderMigration(catalog) {
    const legacy = view?.legacy, section = $('migration'), rows = $('migration-rows');
    const comparison = new Map((delta?.migration || []).map(row => [row.old_key, row]));
    section.hidden = !(legacy?.sectors.length || legacy?.groups.length || Object.keys(legacy?.aliases || {}).length);
    rows.replaceChildren();
    for (const entry of legacy?.sectors || []) {
      const row = doc.createElement('div'), identity = doc.createElement('span');
      row.className = 'mr-tools';
      identity.innerHTML = `<b>${esc(entry.name)}</b> <code>${esc(entry.key)}</code> <small>${quantity(entry.count)} 只</small>`;
      row.append(identity);
      if (legacy.pending) {
        const select = doc.createElement('select');
        select.setAttribute('aria-label', `迁移 ${entry.name}`);
        select.dataset.focusKey = `migration:${entry.key}`;
        select.disabled = busy;
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
        counts.className = 'mr-delta';
        counts.innerHTML = diff.new_key ? [['新名单', diff.new_count], ['共有', diff.common], ['旧独有', diff.old_only],
          ['新独有', diff.new_only]].map(([label, count]) => `<span>${label} <b>${quantity(count)}</b></span>`).join('') : '未匹配';
        row.append(counts);
      }
      rows.append(row);
    }
    for (const group of legacy?.groups || []) {
      const groupText = doc.createElement('p'); groupText.className = 'mr-meta';
      groupText.textContent = `旧组 ${group.name}（${group.boards.join('、')}）`; rows.append(groupText);
    }
    for (const [key, values] of Object.entries(legacy?.aliases || {})) {
      const alias = doc.createElement('p'); alias.className = 'mr-meta';
      alias.textContent = `旧别名 ${key}（${values.join('、')}）`; rows.append(alias);
    }
    $('migration-confirm').hidden = !(legacy?.pending && delta);
  }
  function render() {
    if (disposed) return;
    const catalog = entries(), selected = $('selected'), matches = $('matches'), t = view?.taxonomy;
    const focused = doc.activeElement?.dataset.focusKey;
    const matchScroll = matches.scrollTop;
    const progress = t?.progress?.board_total ? `建库 ${t.progress.board_ready}/${t.progress.board_total} 板块` : '';
    const inventory = [t?.complete ? `${t.stale ? '待更新' : '完整'} ${t.market_day || at(t.observed_ms)}` : '等待完整成分库',
      t?.complete ? `${quantity(t.stock_total)} 只股票` : null,
      t?.hierarchy_status === 'DERIVED_FROM_DC_MEMBERSHIP' ? '东财成分关系重建' : '层级未核实',
      t?.unclassified_count ? `行业未归属 ${t.unclassified_count} 只` : '',
      t?.queued ? '已安排更新' : ''].filter(Boolean);
    const warnings = [progress, t?.error ? statusText(t.error) : '', t?.retry_at ? `下次重试 ${at(t.retry_at * 1000)}` : '']
      .filter(Boolean);
    $('inventory').innerHTML = inventory.map(text => `<span>${esc(text)}</span>`).join('') +
      warnings.map(text => `<span class="mr-note" data-tone="amber">${esc(text)}</span>`).join('');
    selected.replaceChildren();
    for (const key of draft) {
      const entry = catalog.get(key), snapshot = view?.sectors.find(s => s.key === key);
      const count = snapshot?.symbols.length ?? entry?.count;
      const stale = entry?.removed || view?.sector_errors?.[key] ||
        entry?.status === 'STALE' || (snapshot?.data_day && snapshot.data_day < (t?.market_day || snapshot.data_day));
      const name = entry?.name || key;
      const chip = doc.createElement('span');
      chip.className = 'mr-chip';
      chip.innerHTML = `${kindBadge(entry ? kindOf(entry) : 'concept')}<b>${esc(name)}</b>
        <small>${quantity(count)} 只${stale ? ' / 旧快照' : ''}</small>`;
      chip.append(button('', () => { draft = draft.filter(k => k !== key); changed(); },
        { glyph: 'close', label: `移除 ${name}`, disabled: busy }));
      selected.append(chip);
    }
    if (!draft.length) selected.textContent = '未选择板块';
    const terms = splitTerms(input.value);
    const query = terms.length === 1 ? terms[0] : '';
    const treeVisible = !terms.length && ['all', 'industry'].includes(kind);
    const treeCount = renderTree(catalog, treeVisible);
    const groupHits = ['all', 'groups'].includes(kind) && terms.length <= 1 ?
      prefs.groups.filter(group => !query || compact(group.name).includes(compact(query))) : [];
    const hits = terms.length > 1 || kind === 'groups' ? [] : catalogMatches([...catalog.values()], query, kind)
      .filter(entry => !(treeVisible && entry.level >= 1));
    const shown = hits.slice(page * PAGE, (page + 1) * PAGE);
    const count = treeCount + hits.length + groupHits.length;
    const label = treeCount && kind === 'industry' ? '个行业' : '个匹配';
    $('match-count').textContent = `${quantity(count)} ${label}${hits.length > PAGE ? ` / ${page + 1} / ${Math.ceil(hits.length / PAGE)} 页` : ''}`;
    matches.replaceChildren();
    for (const group of groupHits) {
      const result = button('', () => joinGroup(group, catalog),
        { label: `关注组 ${group.name}`, key: `group-match:${group.id}`, disabled: busy });
      result.className = 'mr-group-match';
      result.innerHTML = `${kindBadge('groups')}<b>${esc(group.name)}</b><small>${quantity(group.boards.length)} 个板块</small>${icon('plus')}`;
      matches.append(result);
    }
    matches.append(...shown.map(entry => boardRow(entry)));
    matches.scrollTop = matchScroll;
    $('prev').hidden = page === 0;
    $('next').hidden = (page + 1) * PAGE >= hits.length;
    $('groups').replaceChildren();
    for (const group of prefs.groups) {
      const row = doc.createElement('div'), name = doc.createElement('span');
      row.className = 'mr-group-entry';
      name.innerHTML = `${kindBadge('groups')}<b>${esc(group.name)}</b><small>${quantity(group.boards.length)} 个板块</small>`;
      row.append(name, button('加入关注', () => joinGroup(group, catalog), { glyph: 'plus', label: `加入关注组 ${group.name}`, disabled: busy }),
      button('编辑', () => editGroup(group), { glyph: 'pencil', key: `edit:${group.id}`, label: `编辑组 ${group.name}`, disabled: busy }),
      button('删除', () => {
        prefs.groups = prefs.groups.filter(g => g.id !== group.id);
        if (groupId === group.id) editGroup(null);
        changed();
      }, { glyph: 'trash', label: `删除组 ${group.name}`, disabled: busy }));
      $('groups').append(row);
    }
    $('group-entries').replaceChildren(...groupBoards.map(key => {
      const item = catalog.get(key), name = item?.name || key;
      const chip = doc.createElement('span');
      chip.className = 'mr-chip';
      chip.innerHTML = `${kindBadge(item ? kindOf(item) : 'concept')}<b>${esc(name)}</b>`;
      chip.append(button('', () => { groupBoards = groupBoards.filter(k => k !== key); render(); },
        { glyph: 'close', label: `从组移除 ${name}`, disabled: busy }));
      return chip;
    }));
    const deltaCounts = delta ? [['新增', delta.added, 'blue'], ['退出', delta.removed, 'red'],
      ['保留', delta.retained, 'muted'], ['预览', delta.total, 'teal']] : [];
    $('summary').innerHTML = view ? `<div class="mr-delta">
      <span>已保存 <b>${quantity(view.sectors.length)}</b> 个板块</span>
      ${deltaCounts.filter(([, count]) => Number.isInteger(count)).map(([label, count, tone]) =>
        `<span class="mr-note" data-tone="${tone}">${label} <b>${quantity(count)}</b></span>`).join('')}
      ${!delta && dirty() ? '<span class="mr-note" data-tone="amber">未保存草稿</span>' : ''}</div>`
      : '<span class="mr-meta">正在读取关注池…</span>';
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
    $('migration-check').disabled = busy;
    $('alias-save').disabled = busy;
    $('detail-add').disabled = busy || !detail?.board || draft.includes(detail.query.board);
    $('group-add').disabled = busy || !draft.length;
    $('group-save').disabled = busy;
    $('group-reset').disabled = busy;
    if (focused) [...node.querySelectorAll('[data-focus-key]')].find(item =>
      item.dataset.focusKey === focused)?.focus({ preventScroll: true });
  }
  function update(next, reset = false) {
    if (disposed || !next) return;
    if (!Number.isInteger(next.revision) || !Array.isArray(next.sectors) || !next.members || !Array.isArray(next.catalog)) {
      throw Error('关注池回执无效');
    }
    if (view && next.revision < view.revision) return;
    const clean = !dirty(), sameGeneration = next.revision === revision && (next.taxonomy?.run ?? null) === run;
    if (view?.taxonomy?.run !== next.taxonomy?.run) closeDetail(false);
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
  let lookupTrigger;
  function closeDetail(restore = true) {
    lookupId++; detail = null; $('detail').hidden = true;
    if (restore) {
      const trigger = [...node.querySelectorAll('[data-focus-key]')].find(item => item.dataset.focusKey === lookupTrigger);
      (trigger || input).focus();
    }
  }
  async function lookup(query, offset = 0) {
    const request = ++lookupId;
    const generation = view?.taxonomy?.run;
    if (!generation) return;
    const newDetail = !detail || query !== detail.query;
    if (newDetail) lookupTrigger = doc.activeElement?.dataset.focusKey;
    try {
      const next = await onLookup({ ...query, run: generation, offset });
      if (disposed || request !== lookupId || !next || generation !== view.taxonomy?.run) return;
      detail = { query, ...next };
      paintFocusDetail($('detail'), detail, entries(), prefs.aliases);
      if (newDetail) $('detail-title').focus();
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
  $('kind').addEventListener('click', event => {
    const tab = event.target.closest('[data-kind-value]');
    if (!tab) return;
    kind = tab.dataset.kindValue;
    for (const button of $('kind').children) button.setAttribute('aria-pressed', String(button === tab));
    page = 0; render();
  });
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
  $('close').addEventListener('click', () => closeDetail());
  $('detail-add').addEventListener('click', () => choose(detail.query.board));
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
  return { update, inspect: key => lookup({ board: key }), dispose: () => { disposed = true; node.remove(); } };
}
