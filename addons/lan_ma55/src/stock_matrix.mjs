import { escapeHtml as esc, icon, kindBadge, kindOf, price, quantity, signed } from './presentation.mjs';
import { normalizedTime, PERIOD_LABEL, statusText, TIMEFRAMES } from './workbench_data.mjs';

export const short = value => normalizedTime(value).slice(0, 16) || '—';

export function newest(row) {
  return TIMEFRAMES.map(tf => [tf, row.frames[tf]?.latest_candidate?.at || ''])
    .sort((a, b) => b[1].localeCompare(a[1]))[0];
}

function frameLabel(frame) {
  if (frame?.status !== 'READY') {
    return ['INSUFFICIENT_HISTORY', 'MISSING_COMPLETED_BAR', 'WAITING_FOR_COMPLETED_TARGET', 'WAITING_INPUT']
      .includes(frame?.status) ? '待补' : frame?.status ? '阻塞' : '—';
  }
  const decision = frame.result?.latest;
  if (decision?.event_id) return '候选';
  if (decision?.pressure_test) return '反压';
  return decision?.impulse_recent || decision?.support_test_time ? '观察' : '就绪';
}

export function savedScopes(focus) {
  if (!focus) return [{ key: '', name: '全部' }];
  const members = Object.values(focus.members);
  const groups = (focus.groups || []).map(group => {
    const keys = new Set(group.boards);
    return { key: 'group:' + group.id, name: group.name, boards: group.boards, kind: 'groups',
      count: members.filter(sectors => sectors.some(key => keys.has(key))).length };
  });
  return [{ key: '', name: '全部', count: members.length },
    ...focus.sectors.map(sector => ({ ...sector, count: sector.symbols.length })), ...groups];
}

export function scopeButtons(scopes, selected, query) {
  const matches = scopes.filter(scope => !scope.key || scope.name.toLowerCase().includes(query)
    || scope.key.toLowerCase().includes(query));
  return matches.map(item => `<div class="mr-scope-row">
    <button type="button" class="mr-scope" aria-pressed="${selected === item.key}" data-board="${esc(item.key)}">
      ${item.key ? kindBadge(kindOf(item)) : `<span class="mr-kind" data-kind="all">${icon('layers')}</span>`}
      <b title="${esc(item.name)}">${esc(item.name)}</b><span class="mr-count">${quantity(item.count)}</span>
    </button>${item.key && !item.boards ? `<button type="button" class="mr-icon-button" data-inspect="${esc(item.key)}"
    title="查看 ${esc(item.name)} 成分" aria-label="查看 ${esc(item.name)} 成分">${icon('list')}</button>` : ''}
  </div>`).join('');
}

export function filterRows(rows, scope, { query, state, period, order }) {
  const sectors = new Set(scope.boards || [scope.key]);
  const periods = period === 'all' ? TIMEFRAMES : [period];
  return rows.filter(row => (!scope.key || row.sectors.some(key => sectors.has(key))) &&
    (!query || [row.symbol, row.name].some(value => value?.toLowerCase().includes(query))) &&
    (state === 'all' || periods.some(tf => state === 'blocked'
      ? row.frames[tf]?.status !== 'READY' : frameLabel(row.frames[tf]) === state)))
    .sort((a, b) => (order === 'candidate' ? newest(b)[1].localeCompare(newest(a)[1])
      : order === 'change' ? (b.reference?.change_pct ?? -Infinity) - (a.reference?.change_pct ?? -Infinity)
        : 0) || a.symbol.localeCompare(b.symbol));
}

export function stockRows(rows, selected, empty) {
  return rows.map(row => {
    const symbol = esc(row.symbol), name = esc(row.name);
    const top = row.levels?.pressures?.[0], ref = row.reference, change = ref?.change_pct;
    const cells = TIMEFRAMES.map(tf => {
      const frame = row.frames[tf], label = frameLabel(frame);
      const title = frame?.status === 'READY'
        ? [short(frame.expected_end), frame.latest_candidate?.at ? `最近确认 ${short(frame.latest_candidate.at)}` : ''].filter(Boolean).join(' / ')
        : statusText(frame?.status);
      return `<td data-column="${tf}"><button class="mr-frame" data-stock="${symbol}" data-tf="${tf}"
        data-state="${label}" title="${esc(title)}" aria-label="${name} ${PERIOD_LABEL[tf]} ${label}">${label}</button></td>`;
    }).join('');
    return `<tr class="${selected === row.symbol ? 'selected' : ''}">
      <td><button class="mr-stock-name" data-stock="${symbol}" title="${name}">${name}</button>${name === symbol ? '' : `<span class="mr-code">${symbol}</span>`}</td>
      <td class="mr-price" title="${esc(ref ? `${short(ref.at)} / ${ref.price_basis}` : '等待完成行情')}">
        ${price(ref?.price, row.raw_tick)}<span class="mr-code ${change > 0 ? 'mr-rise' : change < 0 ? 'mr-fall' : ''}">${signed(change)}</span></td>
      ${cells}<td class="mr-price">${top ? `<small>${PERIOD_LABEL[top.timeframe]}</small> ${price(top.ma55, row.raw_tick)}
        <span class="mr-code">${signed(top.distance_pct)}</span>` : '—'}</td></tr>`;
  }).join('') || `<tr><td class="mr-empty" colspan="9">${esc(empty)}</td></tr>`;
}
