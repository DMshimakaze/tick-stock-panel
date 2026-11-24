/** Focus editor for the existing .mr themed page. No transport or chart logic. */
const normalize = value => String(value).normalize('NFKC').trim().toLowerCase();
export const splitTerms = value => [...new Set(String(value).split(/[、,，;；\n]+/).map(t => t.trim()).filter(Boolean))];

export function catalogMatches(catalog, query) {
  const q = normalize(query);
  if (!q) return [];
  return catalog.filter(e => normalize(e.name).includes(q) || normalize(e.key).includes(q))
    .sort((a, b) => Number(normalize(b.name) === q) - Number(normalize(a.name) === q) || a.name.localeCompare(b.name, 'zh-CN') || a.key.localeCompare(b.key));
}

export function resolveTerms(catalog, query, selected = []) {
  const keys = new Set(selected), unresolved = [];
  for (const term of splitTerms(query)) {
    const exact = catalog.filter(e => [normalize(e.name), normalize(e.key)].includes(normalize(term)));
    if (exact.length === 1) keys.add(exact[0].key);
    else unresolved.push(term);
  }
  return { selection: [...keys], unresolved };
}

const equal = (a, b) => a.length === b.length && a.every((key, i) => key === b[i]);
const label = entry => `${entry.name} · ${entry.key.startsWith('industry:') ? '行业' : '概念'}`;

/** Parent supplies backend-acknowledged {revision,sectors,members,catalog,...}.
 * apply/refresh return the acknowledged view, not an optimistic write receipt.
 * Use a stable element outside any periodically replaced results table.
 */
export function mountFocusControls(el, { onApply, onRefresh }) {
  const doc = el.ownerDocument, node = doc.createElement('section');
  node.className = 'mr-panel';
  node.innerHTML = `<h2>关注板块</h2><div class="mr-tools" data-role="selected"></div>
    <div class="mr-tools"><input aria-label="添加同花顺板块" placeholder="输入板块名称，可用顿号分隔" data-role="query"><button data-role="add">添加</button></div>
    <div class="mr-tools" data-role="matches"></div>
    <p data-role="summary" aria-live="polite"></p><p class="mr-error" data-role="error" role="status"></p>
    <div class="mr-tools"><button data-role="apply">保存并更新</button><button data-role="refresh">刷新成分</button><button data-role="reset">撤销未保存修改</button></div>`;
  el.append(node);
  const $ = role => node.querySelector(`[data-role="${role}"]`);
  const input = $('query');
  let view = null, draft = [], base = [], revision = null, busy = false, disposed = false, error = '';
  const dirty = () => !equal(base, draft);
  const conflict = () => dirty() && revision !== view?.revision;
  const catalog = () => [...new Map([...(view?.sectors || []), ...(view?.catalog || [])].map(e => [e.key, e])).values()];
  function button(text, action, title) {
    const b = doc.createElement('button');
    b.type = 'button'; b.textContent = text; b.disabled = busy; b.title = title || text;
    b.addEventListener('click', action); return b;
  }
  function render() {
    if (disposed) return;
    const entries = catalog(), tags = $('selected'), matches = $('matches');
    tags.replaceChildren(); matches.replaceChildren();
    for (const key of draft) {
      const entry = entries.find(e => e.key === key), saved = view?.sectors.find(e => e.key === key);
      const status = view?.sector_errors?.[key] ? ' · 旧快照' : saved ? ` · ${saved.symbols.length}只` : ' · 待核实';
      tags.append(button(`${entry ? label(entry) : key}${status} ×`, () => {
        draft = draft.filter(k => k !== key); error = ''; render();
      }, `移除 ${entry?.name || key}（保存后生效）`));
    }
    if (!draft.length) tags.textContent = '未选择板块；空股池不会启动全市场扫描。';
    const terms = splitTerms(input.value);
    if (terms.length === 1) for (const entry of catalogMatches(entries, terms[0]).slice(0, 12)) {
      matches.append(button(`${label(entry)} · ${entry.key.split(':')[1]}`, () => {
        if (!draft.includes(entry.key)) draft.push(entry.key);
        input.value = ''; error = ''; render(); input.focus();
      }));
    }
    $('summary').textContent = view ? `已保存 ${view.sectors.length} 个板块 · 去重 ${Object.keys(view.members).length} 只股票${dirty() ? ' · 草稿未保存' : ''}${view.catalog_status ? ' · '+view.catalog_status : ''}` : '正在读取关注池…';
    $('error').textContent = conflict() ? '后台关注池已变化，请撤销草稿并重新编辑。' : error;
    $('apply').textContent = draft.length ? '保存并更新' : '暂停并保存';
    $('apply').disabled = !view || busy || !dirty() || conflict() || terms.length > 0;
    $('refresh').disabled = !view || busy || dirty() || terms.length > 0;
    $('reset').disabled = !view || busy || (!dirty() && !terms.length);
    $('add').disabled = busy || !view || !terms.length;
    input.disabled = busy || !view;
  }
  function update(next, reset = false) {
    if (disposed) return;
    if (!next || !Number.isInteger(next.revision) || !Array.isArray(next.sectors) || !next.members || !Array.isArray(next.catalog)) throw Error('关注池回执无效');
    if (view && next.revision < view.revision) { if (reset) throw Error('回执版本已过期'); return; }
    const clean = !dirty(); view = next;
    if (reset || clean) { base = next.sectors.map(e => e.key); draft = [...base]; revision = next.revision; }
    render();
  }
  function add() {
    const r = resolveTerms(catalog(), input.value, draft); draft = r.selection;
    input.value = r.unresolved.join('、');
    error = r.unresolved.length ? '名称未唯一匹配，请选择确切同花顺板块；不会自动扩展为上级板块。' : '';
    render();
  }
  async function submit(refresh) {
    const control = $(refresh ? 'refresh' : 'apply');
    if (control.disabled || disposed) return;
    busy = true; error = ''; render();
    try {
      const next = await (refresh ? onRefresh({ expected_revision: revision }) : onApply({ selection: [...draft], expected_revision: revision }));
      if (!disposed) update(next, true);
    } catch (e) { if (!disposed) error = `操作未确认：${e.message || e}；保留上次已核实显示及草稿，请重读确认。`; }
    finally { busy = false; render(); }
  }
  input.addEventListener('input', render);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); if (!$('add').disabled) add(); } });
  $('add').addEventListener('click', add);
  $('apply').addEventListener('click', () => void submit(false));
  $('refresh').addEventListener('click', () => void submit(true));
  $('reset').addEventListener('click', () => { input.value = ''; error = ''; update(view, true); });
  render();
  return { update, dispose: () => { disposed = true; node.remove(); } };
}
