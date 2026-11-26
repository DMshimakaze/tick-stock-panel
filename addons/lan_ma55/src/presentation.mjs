export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

const PATHS = {
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  arrowUp: '<path d="M7 14l5-5 5 5M12 9v10"/>',
  arrowLeft: '<path d="m14 6-6 6 6 6"/>',
  arrowRight: '<path d="m10 6 6 6-6 6"/>',
  archive: '<path d="M4 7h16v13H4zM3 4h18v3H3zM10 12h4"/>',
  chart: '<path d="M3 4v16h18M7 15l4-4 4 2 5-6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  external: '<path d="M13 4h7v7M20 4l-9 9M19 13v7H4V5h7"/>',
  folder: '<path d="M3 6h7l2 2h9v11H3z"/>',
  industry: '<path d="M3 21V10l6 3V8l6 4 1-9h4l1 18H3Zm4-4h1m4 0h1m4 0h1"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  list: '<path d="M5 6h14M5 12h14M5 18h14"/>',
  pencil: '<path d="m14 6 4 4M5 19l1-5L17 3l4 4L10 18l-5 1Z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14-5L4 8m0-5v5h5m-5 5a8 8 0 0 0 14 5l2-2m0 5v-5h-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/>',
  settings: '<path d="M4 7h16M4 12h16M4 17h16M8 5v4m8 1v4m-5 1v4"/>',
  spark: '<path d="m12 3 2 6 6 3-6 2-2 7-2-7-6-2 6-3 2-6Z"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13m-8 4v5m4-5v5"/>',
  warning: '<path d="M12 4 3 20h18L12 4Zm0 6v4m0 3v.1"/>',
};

export const icon = name => `<svg class="mr-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name]}</svg>`;
export const quantity = value => Number.isInteger(value) ? value.toLocaleString('zh-CN') : '—';
export const signed = value => Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
export const price = (value, tick) => Number.isFinite(value) ? value.toFixed(tick === .001 ? 3 : 2) : '—';
export const codeOf = entry => entry.key.split(':').at(-1);
export function kindOf(entry) {
  const kind = entry.kind || entry.key.split(':').at(-2);
  return kind === 'cn_concept' ? 'concept' : kind;
}
export const KINDS = {
  industry: { label: '行业', icon: 'industry' }, concept: { label: '概念', icon: 'layers' },
  theme: { label: '题材', icon: 'spark' }, groups: { label: '关注组', icon: 'folder' },
};

export function kindBadge(kind) {
  const type = KINDS[kind] || KINDS.concept;
  return `<span class="mr-kind" data-kind="${escapeHtml(kind)}" title="${type.label}">${icon(type.icon)}</span>`;
}
