import { beijing } from './chart_model.mjs';
import { codeOf, escapeHtml as esc, icon, kindBadge, kindOf, KINDS, quantity } from './presentation.mjs';

const verifiedAt = value => Number.isFinite(value) ? beijing(value / 1000).slice(0, 16) : '—';
const facts = pairs => pairs.map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join('');

export const DETAIL = `<section class="mr-taxonomy-detail" aria-label="分类详情" data-role="detail" hidden>
  <header class="mr-panel-head"><div class="mr-detail-heading" data-role="detail-heading">
    <span data-role="detail-kind"></span><div><h3 tabindex="-1" data-role="detail-title"></h3><p class="mr-meta" data-role="detail-code"></p></div>
  </div><div class="mr-actions">
    <button type="button" class="mr-icon-button" data-role="detail-add" title="加入关注草稿" aria-label="加入关注草稿">${icon('plus')}</button>
    <button type="button" class="mr-icon-button" data-role="close" title="关闭分类详情" aria-label="关闭分类详情">${icon('close')}</button>
  </div></header>
  <nav class="mr-breadcrumb" aria-label="行业路径" data-role="detail-source"></nav>
  <dl class="mr-facts mr-detail-facts" data-role="detail-facts"></dl>
  <div class="mr-detail-rows" data-role="detail-rows"></div>
  <div class="mr-pagination">
    <span class="mr-meta" data-role="detail-page"></span>
    <button class="mr-icon-button" data-role="detail-prev" title="成分上一页" aria-label="成分上一页" hidden>${icon('arrowLeft')}</button>
    <button class="mr-icon-button" data-role="detail-next" title="成分下一页" aria-label="成分下一页" hidden>${icon('arrowRight')}</button>
  </div>
  <details class="mr-alias" data-role="alias">
    <summary>板块别名${icon('chevron')}</summary>
    <div class="mr-tools"><input data-role="alias-input" aria-label="自定义别名" placeholder="用顿号分隔多个别名">
      <button data-role="alias-save">保存别名到草稿</button></div>
  </details>
</section>`;

function constituents(rows, hasReasons) {
  const columns = hasReasons ? '<th>入选原因</th>' : '';
  const body = rows.map(row => {
    const symbol = typeof row === 'string' ? row : row.symbol;
    const name = typeof row === 'string' ? row : row.name;
    return `<tr><td><span title="${esc(name)}">${esc(name || symbol)}</span></td>
      <td class="mr-code">${esc(symbol)}</td>${hasReasons ? `<td class="mr-reason">${esc(row.reason || '—')}</td>` : ''}</tr>`;
  }).join('');
  return `<table class="mr-members"><thead><tr><th>股票</th><th>代码</th>${columns}</tr></thead>
    <tbody>${body || `<tr><td colspan="${hasReasons ? 3 : 2}" class="mr-empty">暂无已核实成分</td></tr>`}</tbody></table>`;
}

function memberships(keys, catalog) {
  const entries = keys.map(key => catalog.get(key) || { key, name: codeOf({ key }) });
  return Object.entries(KINDS).filter(([kind]) => kind !== 'groups').map(([kind, type]) => {
    const group = entries.filter(entry => kindOf(entry) === kind);
    if (!group.length) return '';
    return `<section class="mr-lookup-group"><h4>${kindBadge(kind)}${type.label}</h4><ul>${group.map(entry =>
      `<li><span>${esc(entry.name)}</span><code>${esc(codeOf(entry))}</code></li>`).join('')}</ul></section>`;
  }).join('') || '<p class="mr-empty">当前快照没有归属记录</p>';
}

export function paintFocusDetail(section, detail, catalog, aliases) {
  const role = name => section.querySelector(`[data-role="${name}"]`);
  const { board, query, name, symbol, rows, total, offset } = detail;
  const entry = board && (catalog.get(query.board) || board);
  const kind = entry && kindOf({ key: query.board, ...entry });
  const path = (board ? board.path : detail.industry_path) || [];
  role('detail-title').textContent = board?.name || name || symbol;
  role('detail-kind').innerHTML = board ? kindBadge(kind) : icon('target');
  const code = board ? codeOf({ key: query.board }) : symbol;
  role('detail-code').textContent = board ? `${KINDS[kind]?.label || '概念'} / ${code}` : code;
  role('detail-source').innerHTML = path.map(part => `<span>${esc(part)}</span>`).join('');
  role('detail-source').hidden = !path.length;
  role('detail-facts').innerHTML = facts(board ? [
    ['成分股', quantity(total)], ['快照日期', board.data_day || '—'], ['核验时间', verifiedAt(board.observed_ms)],
  ] : [['归属板块', quantity(total)]]);
  role('detail-rows').innerHTML = board ? constituents(rows, kind === 'theme' || rows.some(row => row.reason))
    : memberships(rows, catalog);
  role('detail-page').textContent = total ? `${offset + 1}–${offset + rows.length} / ${quantity(total)}` : '0';
  role('detail-prev').hidden = offset === 0;
  role('detail-next').hidden = offset + rows.length >= total;
  role('detail-add').hidden = !board;
  role('alias').hidden = !board;
  role('alias-input').value = (aliases[query.board] || []).join('、');
  section.hidden = false;
  role('detail-rows').scrollTop = 0;
}
