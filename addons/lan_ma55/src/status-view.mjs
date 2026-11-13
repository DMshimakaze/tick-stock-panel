/** Presentation of published business facts; no clock-based trading-day guesses. */
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const count = value => Number.isFinite(value) ? value.toLocaleString('zh-CN') : '—';
const at = value => value ? value.slice(0, 16) : '尚未核实';
const operations = { COLLECTING_5M: '正在采集完成的5m', AGGREGATING_15M: '正在生成完整15m K线',
  CALCULATING_MA55: '正在计算MA55', PUBLISHING: '正在发布结果', IDLE: '等待调度', BLOCKED: '处理受阻' };
const modes = { INITIAL_HISTORY: '初次历史预热', HISTORY_BACKFILL: '历史补齐中', LIVE_CATCHUP: '盘中增量追赶',
  LIVE_CURRENT: '已跟上当前完成K线', WAIT_NEXT_BAR: '已跟上目标，等待下一根', MARKET_CLOSED: '非交易时段，等待下一交易时段', UNKNOWN: '交易日或处理目标尚未核实' };

export function statusModel(s = {}, now = Date.now()) {
  const reason = s.blocking_reason;
  let label = '状态尚未核实', tone = 'muted';
  if (s.phase === 'STARTING') label = '后台启动中';
  else if ((!reason && s.phase === 'QUOTA_WAIT') || reason === 'QUOTA_WAIT') label = '等待额度';
  else if (reason === 'HOST_BUSY') label = '礼让宿主';
  else if (['CALENDAR_LAG', 'SOURCE_LAG', 'FACTOR_PENDING', 'PUBLISH_FAILED'].includes(reason)) {
    label = reason === 'PUBLISH_FAILED' ? '发布受阻' : reason === 'FACTOR_PENDING' ? '部分受阻' : '数据滞后'; tone = 'warning';
  } else if (reason || s.error || s.worker_alive === false) { label = '受阻'; tone = 'danger'; }
  else if (s.sync_mode === 'WAIT_NEXT_BAR' || s.sync_mode === 'LIVE_CURRENT') { label = '等待下一根'; tone = 'accent'; }
  else if (s.sync_mode === 'LIVE_CATCHUP') { label = '盘中追赶中'; tone = 'accent'; }
  else if (['INITIAL_HISTORY', 'HISTORY_BACKFILL'].includes(s.sync_mode)) { label = '补齐历史'; tone = 'accent'; }
  else if (s.sync_mode === 'MARKET_CLOSED') label = '等待开市';
  let operation = operations[s.active_operation] || '尚未收到运行状态';
  if (s.active_operation === 'IDLE' && s.sync_mode === 'WAIT_NEXT_BAR') operation = '等待下一根完成15m';
  if (s.work_stage === 'HISTORY' && ['COLLECTING_5M','AGGREGATING_15M'].includes(s.active_operation)) operation += ' · 历史补齐';
  if (s.active_operation === 'IDLE' && ['SOURCE_LAG','FACTOR_PENDING'].includes(reason)) operation = '等待可用输入，保留上次有效结果';
  if (s.phase === 'QUOTA_WAIT') operation = `等待${s.work_stage === 'HISTORY' ? '历史回补' : '请求'}额度${s.wait_seconds > 0 ? ` · 约${Math.ceil(s.wait_seconds)}秒` : ''}`;
  if (s.phase === 'HOST_BUSY') operation = '等待宿主数据任务完成';
  const stamp = s.status_at ? Date.parse(s.status_at.replace(' ','T') + '+08:00') : NaN;
  const stale = Number.isFinite(stamp) && now - stamp > 120_000;
  if (stale) { label='状态已过期';tone='warning';operation='尚未收到新的后台状态'; }
  return { label, tone, operation, mode: modes[s.sync_mode] || '尚未核实', reason, stale };
}

function progress(label, value, expected, note) {
  const known = Number.isFinite(value) && Number.isFinite(expected) && expected > 0;
  const percent = known ? Math.min(100, Math.max(0, value / expected * 100)) : 0;
  return `<div class="mr-progress-item"><div class="mr-progress-label"><span>${label}</span><b>${count(value)} / ${count(expected)}</b></div>
    <div class="mr-progress-track" role="progressbar" aria-label="${label}" ${known ? `aria-valuemin="0" aria-valuemax="${expected}" aria-valuenow="${value}"` : 'aria-valuetext="尚未核实"'}><span style="width:${percent}%"></span></div><small>${escapeHtml(note)}</small></div>`;
}

export function renderStatus(s, busy = false, readError = '') {
  const view = statusModel(s), data = s.monitoring_verified === false ? {} : s.refresh || {}, analysis = s.monitoring_verified === false ? {} : s.analysis || {};
  const cell = (label, value, sub, cls = '') => `<div class="mr-kpi ${cls}"><span>${label}</span><strong>${value}</strong><small>${escapeHtml(sub)}</small></div>`;
  const fraction = (value, expected) => `${count(value)}<em> / ${count(expected)}</em>`;
  const target = s.data_target_15m;
  const buy = s.pending_publish ? '—' : count(s.latest_day_buy_count);
  const notice = readError || (view.stale ? '后台状态超过2分钟未发布，以下为上次有效数据。' : s.blocking_message || s.error || s.publisher_error);
  return `<header class="mr-header"><div class="mr-heading"><div class="mr-title"><h1>MA55 选股</h1><span class="mr-badge mr-tone-${view.tone}" role="status">${escapeHtml(readError ? '展示读取失败' : view.label)}</span></div>
    <p>15m 结构扫描 · TickFlow 5m → 完整15m · 数据截至 ${escapeHtml(at(s.latest_saved_15m))}</p></div>
    <button data-action="refresh" title="仅读取最新已发布状态，不触发行情采集" ${busy ? 'disabled' : ''}>${busy ? '读取中…' : '刷新展示'}</button></header>
    <section class="mr-kpis" aria-label="MA55扫描状态">
      ${cell('监控股票', count(s.monitored_stock_count), '有效监控范围')}
      ${cell('监控 ETF', count(s.monitored_etf_count), '沪深 ETF · 独立计数')}
      ${cell('已核实15m目标', escapeHtml(target ? target.slice(11, 16) : '—'), target ? target.slice(0, 10) + ' · 北京时间' : '交易日尚未核实', 'mr-kpi-target')}
      ${cell('K线覆盖', fraction(data.refreshed, data.expected), '目标桶已完整保存')}
      ${cell('MA55 已计算', fraction(analysis.calculated, analysis.expected), '目标桶实际算法输出')}
      ${cell('最新交易日 BUY', buy, `${s.latest_buy_date || '尚未核实'} · ${s.pending_publish ? '结果发布中' : '已保存确认'}`)}
    </section>
    <section class="mr-runtime mr-tone-${view.tone}" aria-label="运行与数据状态">
      <div class="mr-runtime-lines"><p><span>当前操作</span><strong>${escapeHtml(view.operation)}</strong></p>
      <p><span>数据模式</span><strong>${escapeHtml(view.mode)}</strong>${Number.isFinite(s.history_queue_count) ? `<small class="mr-history-queue">历史队列 ${count(s.history_queue_count)}</small>` : ''}</p></div>
      ${notice ? `<p class="mr-notice" role="status">${escapeHtml(notice)}</p>` : ''}
      <div class="mr-progress-grid">${progress('K线覆盖', data.refreshed, data.expected, `目标 ${at(target)} / 已保存 ${at(s.latest_saved_15m)}`)}
      ${progress('MA55分析', analysis.calculated, analysis.expected, `已分析 ${at(s.latest_analyzed_15m)} / 已发布 ${at(s.last_publish_at)}`)}</div>
      <div class="mr-runtime-foot"><span>最后成功 ${escapeHtml(at(s.last_success_at))}</span><span>${s.today_session_verified ? '今日交易日已核实' : '今日交易日尚未核实'}${s.calendar?.daily_last ? ' · 日K日历截至 ' + escapeHtml(s.calendar.daily_last) : ''}</span></div>
    </section>`;
}
