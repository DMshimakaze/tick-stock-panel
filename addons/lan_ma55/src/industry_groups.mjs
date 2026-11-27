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

export function industryGroups(items, rowOf, nodes, paths, scope) {
  const groups = new Map();
  const depth = scope.kind === 'industry' ? Math.min(3, (scope.level || 2) + 1) : 3;
  for (const item of items) {
    const value = paths[rowOf(item).symbol];
    const path = typeof value === 'string' ? industryPath(nodes, value) : [];
    const verified = path.length === 3 && (scope.kind !== 'industry' || path.some(node => node.key === scope.key));
    const branch = verified ? path.slice(0, depth) : [];
    const leaf = branch.at(-1);
    const ambiguous = Array.isArray(value) || path.length === 3;
    const key = leaf?.key || (ambiguous ? 'ambiguous' : 'unclassified');
    if (!groups.has(key)) groups.set(key, {
      key, verified, name: leaf?.name || (ambiguous ? '行业归属待核实' : '未分类'),
      parents: branch.slice(0, -1).map(node => node.name).join(' / '), items: [],
    });
    groups.get(key).items.push(item);
  }
  return [...groups.values()].sort((a, b) => Number(b.verified) - Number(a.verified) ||
    industryOrder.compare(`${a.parents}/${a.name}`, `${b.parents}/${b.name}`) || a.key.localeCompare(b.key));
}

export function groupButton(group, list, collapsed) {
  return `<button class="mr-group-toggle" type="button" data-${list}-group="${esc(group.key)}" aria-expanded="${!collapsed}">
    ${icon('chevron')}<strong>${esc(group.name)}</strong>${group.parents ? `<small>${esc(group.parents)}</small>` : ''}
    <span class="mr-count">${quantity(group.items.length)} 只</span></button>`;
}
