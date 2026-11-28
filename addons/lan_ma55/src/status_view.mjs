import { beijing } from './chart_model.mjs';
import { activationAccess } from './activation_results.mjs';
import { escapeHtml as esc, icon, quantity } from './presentation.mjs';
import { normalizedTime, PERIOD_LABEL, statusText, TIMEFRAMES } from './workbench_data.mjs';

const STALE_MS = 45_000;
const STAGES = {
  STARTING: ['启动中', 'muted', 'clock'],
  A: ['更新行情', 'blue', 'refresh'],
  B: ['修复 15m 历史', 'amber', 'archive'],
  C: ['补齐长周期', 'amber', 'archive'],
  F: ['核验复权', 'violet', 'check'],
  D: ['更新日线', 'blue', 'refresh'],
  W: ['补齐周线历史', 'amber', 'archive'],
  ANALYZING: ['待算', 'violet', 'activity'],
  QUOTA_WAIT: ['等待额度', 'amber', 'clock'],
  RATE_LIMITED: ['限流等待', 'amber', 'clock'],
  HOST_BUSY: ['等待宿主', 'muted', 'clock'],
  HOST_STATUS_UNAVAILABLE: ['宿主状态待确认', 'amber', 'warning'],
  WAITING_NEXT_15M: ['等待下一根', 'muted', 'clock'],
  CALENDAR_PENDING: ['交易日历待核实', 'violet', 'clock'],
  ERROR: ['运行异常', 'red', 'warning'],
  SOURCE_UNAVAILABLE: ['行情不可用', 'red', 'warning'],
  CREDENTIAL_REQUIRED: ['凭据异常', 'red', 'warning'],
  STOPPED: ['后台已停止', 'muted', 'clock'],
};
const ACTIVE = new Set(['COLLECTING_NATIVE15', 'COLLECTING_DAILY', 'CHECKING_FACTORS', 'ANALYZING_MA55']);
const printableTime = value => normalizedTime(value).slice(0, 16) || '—';
const epochTime = value => Number.isFinite(value) ? beijing(value / 1000).slice(0, 16) : '—';
const pair = (label, value) => `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`;

export function statusModel(status, view, { now = Date.now(), statusError, viewError } = {}) {
  const s = status || {};
  const olderFocus = Number.isInteger(view?.focus.revision) && s.focus_revision < view.focus.revision;
  const sameFocus = view && s.focus_revision === view.focus.revision &&
    s.taxonomy_run === (view.focus.taxonomy?.run ?? null);
  const linked = sameFocus && Boolean(s.result_id) && s.result_id === view.result_id;
  const coverage = linked ? s.coverage : null;
  const total = olderFocus ? null : s.scope_count;
  const knownTime = Number.isFinite(s.status_at_ms);
  const stale = knownTime && now - s.status_at_ms > STALE_MS;
  const running = knownTime && !stale && !statusError && s.worker_alive === true && ACTIVE.has(s.active_operation);
  const work = STAGES[s.work_stage]?.[0];
  let appearance = STAGES[s.phase] || ['状态待确认', 'muted', 'clock'];
  if (s.phase === 'SCOPE_PAUSED') appearance = [
    s.focus_revision || s.saved_sectors ? '监控已暂停' : '未启用监控', 'muted', 'clock',
  ];
  if (running && s.active_operation === 'ANALYZING_MA55') appearance = ['计算 MA55', 'violet', 'activity'];
  if (!knownTime) appearance = ['状态待确认', 'muted', 'clock'];
  if (s.worker_alive === false && s.phase !== 'ERROR' && knownTime) appearance = STAGES.STOPPED;
  if (stale) appearance = ['状态过期', 'amber', 'warning'];
  if (statusError || viewError) appearance = ['读取失败', 'red', 'warning'];
  const [label, tone, glyph] = appearance;
  const notice = statusError || viewError || (stale ? '当前展示上次有效结果' : s.publisher_error || s.command_error)
    || (s.result_id && !coverage && total > 0 ? '等候与当前关注范围、目标一致的观察结果' : '');
  return {
    label, tone, glyph, running: running && !viewError, notice, total, coverage, sameFocus,
    savedSectors: olderFocus ? null : s.saved_sectors,
    taskNote: running && !viewError ? [s.active_operation === 'ANALYZING_MA55' ? work : null,
      Number.isInteger(s.active_symbols) ? `本批 ${quantity(s.active_symbols)} 只` : null].filter(Boolean).join(' / ')
      : knownTime && !stale && !statusError && ['QUOTA_WAIT', 'RATE_LIMITED'].includes(s.phase) && work
        ? work : '',
  };
}

function metric(label, value, hint, tone, glyph) {
  return `<div class="mr-kpi" data-tone="${tone}">
    <div class="mr-kpi-label">${label}<span class="mr-glyph">${icon(glyph)}</span></div>
    <strong>${quantity(value)}</strong><small>${esc(hint || ' ')}</small>
  </div>`;
}

function periods(coverage) {
  return TIMEFRAMES.map(tf => {
    const ready = coverage?.ready_by_timeframe?.[tf];
    return `<div><span>${PERIOD_LABEL[tf]}</span><b>${quantity(ready)}</b><small>${Number.isInteger(ready) ?
      `/ ${quantity(coverage.expected)}` : ''}</small></div>`;
  }).join('');
}

const BLOCK_GROUPS = [
  ['行情与回放', ['TRADING_HALTED_VERIFIED', 'MISSING_COMPLETED_BAR', 'WAITING_FOR_COMPLETED_TARGET', 'GAP_IN_REPLAY_WINDOW', 'ZERO_VOLUME_BAR']],
  ['历史深度', ['INSUFFICIENT_HISTORY']],
  ['复权', ['FACTOR_PENDING']],
  ['输入核实', ['TICK_UNAVAILABLE', 'METADATA_PENDING', 'WAITING_INPUT']],
];

function blocking(coverage, usable) {
  const summary = new Map();
  for (const period of TIMEFRAMES) {
    for (const [reason, count] of Object.entries(coverage?.blocking_by_timeframe?.[period] || {})) {
      if (!summary.has(reason)) summary.set(reason, []);
      summary.get(reason).push({ period, count });
    }
  }
  return BLOCK_GROUPS.map(([group, reasons]) => {
    const lines = reasons.filter(reason => summary.has(reason))
      .map(reason => `<li><span>${esc(statusText(reason))}</span><div>${summary.get(reason).map(({ period, count }) =>
        `<button type="button" data-block-period="${period}" data-block-reason="${esc(reason)}"
          ${usable.includes(period) ? '' : 'disabled'} title="查看全池匹配股票">${PERIOD_LABEL[period]} ${quantity(count)}</button>`).join('')}</div></li>`);
    return lines.length ? `<div><h3>${group}</h3><ul>${lines.join('')}</ul></div>` : '';
  }).join('');
}

export function chartDiagnosis(detail) {
  if (!detail) return '';
  const timestamp = value => normalizedTime(value) || '未知';
  const retry = detail.queue === 'COOLING' ? `冷却至 ${timestamp(detail.retry_at)}` : detail.queue === 'ELIGIBLE'
    ? '待后台按现有额度调度' : '未知';
  const request = detail.requested_at ? [STAGES[detail.stage]?.[0], timestamp(detail.requested_at),
    detail.error ? statusText(detail.error) : ''].filter(Boolean).join(' · ') : '未知';
  const interval = bounds => bounds[0] === bounds[1] ? timestamp(bounds[0]) : bounds.map(timestamp).join(' 至 ');
  const capacity = detail.listed_capacity;
  const halt = detail.verified_halt;
  return `<dl class="mr-facts" aria-label="逐股数据诊断">${[
    pair('当前目标', timestamp(detail.target)), pair('数据状态', statusText(detail.reason)),
    detail.missing_raw ? pair('已核实缺柱区间', interval(detail.missing_raw)) : '',
    detail.zero_raw ? pair('本地零成交柱', interval(detail.zero_raw)) : '',
    capacity ? pair('上市后完成柱上限', `${detail.listing_date} 上市 · ${capacity.listed}/${capacity.required} 根`) : '',
    detail.factor_day ? pair('已核实因子截至', detail.factor_day) : '',
    halt ? pair('已核实停牌区间', `${halt.from_day} 至 ${halt.through_day} · ${halt.notice}`) : '',
    detail.replay_start ? pair('固定回放起点', timestamp(detail.replay_start)) : '',
    pair(detail.source === 'daily' ? '最近有效日源' : '最近有效原生 15m', timestamp(detail.last_bar)),
    pair('最近请求', request), pair('最近请求成功', timestamp(detail.last_success)), pair('重试状态', retry),
  ].join('')}</dl>`;
}

export function paintRuntime(root, status, view, errors) {
  const s = status || {};
  const model = statusModel(status, view, errors);
  const access = activationAccess(view, status, errors);
  const role = name => root.querySelector(`[data-role="${name}"]`);
  const { coverage, total } = model;
  const ready = coverage?.ready_15m;
  const denominator = coverage?.expected;
  const known = Number.isInteger(ready) && denominator > 0;
  const task = role('task');
  const tone = ready > 0 && ready === denominator ? 'teal' : 'blue';
  const target = printableTime(s.target_15m), update = epochTime(s.result_published_ms);
  role('metrics').innerHTML = [
    metric('监控股票', total, Number.isInteger(model.savedSectors) ? `已保存 ${quantity(model.savedSectors)} 个板块` : '', 'blue', 'activity'),
    metric('15m 就绪', ready, Number.isInteger(denominator) ? `有效范围 ${quantity(denominator)} 只` : '', tone, 'check'),
    metric('激活股票', access.usable.length ? coverage?.activation_symbols : null,
      '六周期有效窗口去重', 'violet', 'spark'),
    metric('15m 未就绪', coverage?.unready_15m, '待补与阻塞', 'amber', 'clock'),
  ].join('');
  const statusChip = role('task-chip');
  statusChip.dataset.tone = model.tone;
  if (statusChip.dataset.label !== model.label) {
    statusChip.dataset.label = model.label;
    statusChip.innerHTML = `${icon(model.glyph)}<span>${model.label}</span>`;
  }
  task.dataset.tone = model.tone;
  task.dataset.running = String(model.running);
  role('task-icon').innerHTML = icon(model.glyph);
  role('task-label').textContent = model.label;
  role('task-note').textContent = model.taskNote;
  role('target').textContent = target;
  role('result-time').textContent = update;
  const progress = role('progress');
  progress.hidden = !known;
  role('coverage').textContent = known ? `${quantity(ready)} / ${quantity(denominator)}`
    : denominator === 0 ? '关注池为空' : '—';
  progress.setAttribute('aria-valuemin', '0');
  if (known) {
    progress.setAttribute('aria-valuemax', denominator);
    progress.setAttribute('aria-valuenow', ready);
    progress.firstElementChild.style.width = `${ready * 100 / denominator}%`;
  }
  role('notice').textContent = model.notice;
  role('period-coverage').innerHTML = periods(coverage);
  role('runtime-facts').innerHTML = [
    pair('15m 行情到达', Number.isInteger(coverage?.arrived_15m) ?
      `${quantity(coverage.arrived_15m)} / ${quantity(denominator)}` : '—'),
    pair('行情水位', printableTime(model.sameFocus && s.latest_saved_15m)),
    pair('分析水位', printableTime(model.sameFocus && s.latest_analyzed_15m)),
    pair('后台心跳', epochTime(s.status_at_ms)),
    pair('全周期齐套', quantity(coverage?.ready_all)),
    pair('当前批次', Number.isInteger(s.active_symbols) && model.running ? `${quantity(s.active_symbols)} 只` : '—'),
  ].join('');
  const reasons = blocking(coverage, access.usable) || (coverage ? '<p class="mr-meta">当前结果没有已知阻塞</p>' : '');
  const waiting = ['QUOTA_WAIT', 'RATE_LIMITED'].includes(s.phase) ? statusText(s.phase) : null;
  const host = s.host_job_status?.state;
  const hostLabel = { IDLE: '空闲', BUSY: '同步中', UNAVAILABLE: '暂不可核实' }[host];
  const blocks = role('blocking');
  if (blocks.dataset.markup !== reasons) {
    const { blockPeriod, blockReason } = root.ownerDocument.activeElement.dataset;
    blocks.innerHTML = reasons;
    blocks.dataset.markup = reasons;
    if (blockReason) [...blocks.querySelectorAll('button')].find(button =>
      button.dataset.blockPeriod === blockPeriod && button.dataset.blockReason === blockReason)?.focus({ preventScroll: true });
  }
  role('system-facts').innerHTML = [
    pair('额度', waiting || (s.waiting_for && !s.waiting_for.startsWith('HOST_') ? statusText(s.waiting_for) : '—')),
    pair('宿主任务', hostLabel || '—'),
    pair('行情异常', ['ERROR', 'SOURCE_UNAVAILABLE', 'CREDENTIAL_REQUIRED'].includes(s.phase) ? statusText(s.phase) : '—'),
  ].join('');
  const dc = view?.focus.taxonomy;
  role('source-facts').innerHTML = [
    pair('东财快照', dc?.market_day || '—'),
    pair('分类状态', dc?.error ? statusText(dc.error) : dc?.stale ? '待更新' : dc?.complete ? '完整' : '—'),
    pair('分类范围', quantity(dc?.stock_total)),
    pair('规则', view?.rule_version || '—'),
  ].join('');
  role('diagnostics').textContent = JSON.stringify({ status: status || null, classification: dc || null }, null, 2);
}
