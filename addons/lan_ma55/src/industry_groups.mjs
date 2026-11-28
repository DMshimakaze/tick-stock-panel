import { escapeHtml as esc, icon, quantity } from './presentation.mjs';

const industryOrder = new Intl.Collator('zh-CN');

export function industryNodes(catalog = []) {
  return new Map(catalog.filter(node => node.kind === 'industry').map(node => [node.key, node]));
}

export function industryPath(nodes, key) {
  const path = [];
  for (let node = nodes.get(key); node && path.length < 3; node = nodes.get(node.parent)) path.unshift(node);
  return path.every((node, i) => node.level === i + 1) ? path : [];
}

export function industryGroups(rows, nodes, paths, scope, expanded) {
  const depth = !scope.key ? 1 :
    scope.kind === 'industry' && (scope.level === 1 || scope.level === 2) && expanded.has(scope.key) ?
      scope.level + 1 : null;
  if (!depth) return null;
  const entries = rows.map(row => {
    const value = paths[row.symbol];
    return { row, path: typeof value === 'string' ? industryPath(nodes, value) : [], ambiguous: Array.isArray(value) };
  });
  function partition(items, level) {
    const groups = new Map();
    for (const entry of items) {
      const { path } = entry;
      const node = path.length === 3 && (!scope.key || path[scope.level - 1]?.key === scope.key) ? path[level - 1] : null;
      const ambiguous = entry.ambiguous || path.length === 3;
      const key = node?.key || (ambiguous ? 'ambiguous' : 'unclassified');
      if (!groups.has(key)) groups.set(key, {
        key, level, verified: Boolean(node), name: node?.name || (ambiguous ? '行业归属待核实' : '未分类'), items: [],
      });
      groups.get(key).items.push(entry);
    }
    return [...groups.values()].sort((a, b) => Number(b.verified) - Number(a.verified) ||
      industryOrder.compare(a.name, b.name) || a.key.localeCompare(b.key)).map(group => {
      const expandable = group.verified && level < 3;
      const children = expandable && expanded.has(group.key) ? partition(group.items, level + 1) : null;
      return { ...group, expandable, children, items: group.items.map(entry => entry.row) };
    });
  }
  return partition(entries, depth);
}

export function groupButtons(group, collapsed) {
  return `<div class="mr-matrix-group-actions">
    <button class="mr-group-toggle" type="button" data-matrix-group="${esc(group.key)}" aria-expanded="${!collapsed}">
    ${icon('chevron')}<strong>${esc(group.name)}</strong><span class="mr-count">${quantity(group.items.length)} 只</span></button>
    ${!collapsed && group.expandable ? `<button type="button" data-industry-detail="${esc(group.key)}"
      aria-label="${esc(group.name)}的下级行业" aria-pressed="${Boolean(group.children)}">
      ${group.children ? '收起细分' : '细分'}</button>` : ''}</div>`;
}
