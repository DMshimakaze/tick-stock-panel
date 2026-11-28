import { escapeHtml as esc, icon, kindBadge, kindOf, price, quantity, signed } from './presentation.mjs';
import { normalizedTime, PERIOD_LABEL, statusText, TIMEFRAMES } from './workbench_data.mjs';
import { frameActivation, matrixSignal, STATE_LABEL } from './activation_results.mjs';
import { groupButtons, industryPath } from './industry_groups.mjs';

export const short = value => normalizedTime(value).slice(0, 16) || '—';

export function newest(row, period, access) {
  const frames = period === 'all' ? TIMEFRAMES : [period];
  return frames.map(tf => [tf, matrixSignal(row, tf, access)?.at || ''])
    .sort((a, b) => b[1].localeCompare(a[1]))[0];
}

const frameState = (row, tf, access) => frameActivation(row, tf, access)?.state || 'PENDING';
const stateMark = {
  ACTIVATED: '●', INACTIVE: '—', PENDING: icon('clock'), MISSING: icon('warning'),
};
export const matrixLegend = Object.entries(STATE_LABEL).map(([state, label]) =>
  `<span data-state="${state}"><i aria-hidden="true">${stateMark[state]}</i>${label}</span>`).join('');

export function savedScopes(focus, nodes) {
  if (!focus) return [{ key: '', name: '全部' }];
  const members = Object.values(focus.members);
  const groups = (focus.groups || []).map(group => {
    const keys = new Set(group.boards);
    return { key: 'group:' + group.id, name: group.name, boards: group.boards, kind: 'groups',
      count: members.filter(sectors => sectors.some(key => keys.has(key))).length };
  });
  return [{ key: '', name: '全部', count: members.length },
    ...focus.sectors.map(sector => ({ ...sector, kind: kindOf(sector), level: nodes.get(sector.key)?.level,
      aliases: focus.aliases?.[sector.key] || [], count: sector.symbols.length })), ...groups];
}

function scopeButton(item, selected) {
  return `<div class="mr-scope-row">
    <button type="button" class="mr-scope" aria-pressed="${selected === item.key}" data-board="${esc(item.key)}">
      ${item.key ? kindBadge(kindOf(item)) : `<span class="mr-kind" data-kind="all">${icon('layers')}</span>`}
      <b title="${esc(item.name)}">${esc(item.name)}</b><span class="mr-count">${quantity(item.count)}</span>
    </button>${item.key && !item.boards ? `<button type="button" class="mr-icon-button" data-inspect="${esc(item.key)}"
    title="查看 ${esc(item.name)} 成分" aria-label="查看 ${esc(item.name)} 成分">${icon('list')}</button>` : ''}
  </div>`;
}

export function scopeButtons(scopes, selected, query, nodes) {
  const matches = scopes.slice(1).filter(scope => [scope.name, scope.key, ...(scope.aliases || []),
    ...industryPath(nodes, scope.key).map(node => node.name)].some(value => value.toLowerCase().includes(query)));
  return [scopes[0], ...matches].map(item => scopeButton(item, selected)).join('');
}

export function scopedRows(rows, scope, query) {
  const sectors = new Set(scope.boards || [scope.key]);
  return rows.filter(row => (!scope.key || row.sectors.some(key => sectors.has(key))) &&
    (!query || [row.symbol, row.name].some(value => value?.toLowerCase().includes(query))));
}

export function filterRows(rows, { state, period, order, reason }, access) {
  const periods = period === 'all' ? TIMEFRAMES : [period];
  const filtered = rows.filter(row => (!reason || (access.usable.includes(period) && row.frames[period]?.status === reason))
    && (state === 'all' || periods.some(tf => frameState(row, tf, access) === state)));
  const times = order === 'candidate' ? new Map(filtered.map(row => [row.symbol, newest(row, period, access)[1]])) : null;
  return filtered.sort((a, b) => (times ? times.get(b.symbol).localeCompare(times.get(a.symbol))
      : order === 'change' ? (b.reference?.change_pct ?? -Infinity) - (a.reference?.change_pct ?? -Infinity)
        : 0) || a.symbol.localeCompare(b.symbol));
}

export function stockRows(rows, groups, collapsed, selected, empty, access) {
  const visible = [];
  const rowMarkup = row => {
    visible.push(row);
    const symbol = esc(row.symbol), name = esc(row.name);
    const top = row.levels?.pressures?.[0], ref = row.reference, change = ref?.change_pct;
    const cells = TIMEFRAMES.map(tf => {
      const frame = row.frames[tf], state = frameState(row, tf, access);
      const signal = matrixSignal(row, tf, access), label = STATE_LABEL[state];
      const title = [access.targets[tf] ? `目标 ${short(access.targets[tf])}` : '等待该周期完成 K 线',
        access.usable.includes(tf) && frame?.status !== 'READY' ? statusText(frame?.status) : null,
        signal ? `确认 ${short(signal.at)}` : null].filter(Boolean).join(' · ');
      return `<td data-column="${tf}"><button class="mr-frame" data-stock="${symbol}" data-tf="${tf}"
        data-state="${state}"
        title="${esc(`${label} · ${title}`)}" aria-label="${name} ${PERIOD_LABEL[tf]} ${label}"><span aria-hidden="true">${stateMark[state]}</span></button></td>`;
    }).join('');
    return `<tr class="${selected === row.symbol ? 'selected' : ''}">
      <td><button class="mr-stock-name" data-stock="${symbol}" title="${name}">${name}</button>${name === symbol ? '' : `<span class="mr-code">${symbol}</span>`}</td>
      <td class="mr-price" title="${esc(ref ? `${short(ref.at)} / ${ref.price_basis}` : '等待完成行情')}">
        ${price(ref?.price, row.raw_tick)}<span class="mr-code ${change > 0 ? 'mr-rise' : change < 0 ? 'mr-fall' : ''}">${signed(change)}</span></td>
      ${cells}<td class="mr-price">${top ? `<small>${PERIOD_LABEL[top.timeframe]}</small> ${price(top.ma55, row.raw_tick)}
        <span class="mr-code">${signed(top.distance_pct)}</span>` : '—'}</td></tr>`;
  };
  const renderGroups = items => items.map(group => {
    const closed = collapsed.has(group.key);
    return `<tr class="mr-matrix-group" data-level="${group.level}"><th colspan="9">${groupButtons(group, closed)}</th></tr>` +
      (closed ? '' : group.children ? renderGroups(group.children) : group.items.map(rowMarkup).join(''));
  }).join('');
  const markup = groups ? renderGroups(groups) : rows.map(rowMarkup).join('');
  return { markup: markup || `<tr><td class="mr-empty" colspan="9">${esc(empty)}</td></tr>`, visible };
}
