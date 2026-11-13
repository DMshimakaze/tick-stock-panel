/** Presentation of published business facts; no clock-based trading-day guesses. */
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const count = value => Number.isFinite(value) ? value.toLocaleString('zh-CN') : '—';
const at = value => value ? value.slice(0, 16) : '尚未核实';
const operations = { COLLECTING_NATIVE15:'采集原生15m', CHECKING_FACTORS:'补因子', CALCULATING_NATIVE15_CANDIDATES:'计算候选', PUBLISHING:'发布结果', IDLE:'等待调度', BLOCKED:'处理受阻' };
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
  else if (['INITIAL_HISTORY', 'HISTORY_BACKFILL'].includes(s.sync_mode)) { label = '补齐历史'; tone = 'history'; }
  else if (s.sync_mode === 'MARKET_CLOSED') label = '等待开市';
  const stage = {A:'今日增量追赶', B:'最低预热', F:'补因子', C:'深历史补齐'}[s.work_stage];
  let operation = stage || operations[s.active_operation] || '尚未收到运行状态';
  if (s.active_operation === 'IDLE' && s.sync_mode === 'WAIT_NEXT_BAR') operation = '等待下一根完成15m';
  if (s.phase === 'WAITING_NEXT_SESSION') operation = '等待下一交易日';
  if (s.phase === 'QUOTA_WAIT') operation += ` · 等待额度${s.wait_seconds > 0 ? ` ${Math.ceil(s.wait_seconds)}秒` : ''}`;
  if (s.phase === 'HOST_BUSY') operation = '等待宿主数据任务完成';
  if (s.active_operation === 'BLOCKED') operation = '处理受阻';
  const stamp = s.status_at ? Date.parse(s.status_at.replace(' ','T') + '+08:00') : NaN;
  const stale = Number.isFinite(stamp) && now - stamp > 120_000;
  if (stale) { label='状态已过期';tone='warning';operation='尚未收到新的后台状态'; }
  return { label, tone, operation, mode: modes[s.sync_mode] || '尚未核实', reason, stale };
}

function progress(label, value, expected) {
  const known = Number.isFinite(value) && Number.isFinite(expected) && expected > 0;
  const percent = known ? Math.min(100, Math.max(0, value / expected * 100)) : 0;
  return `<div class="mr-progress-item"><div class="mr-progress-track" role="progressbar" aria-label="${label}" ${known ? `aria-valuemin="0" aria-valuemax="${expected}" aria-valuenow="${value}"` : 'aria-valuetext="尚未核实"'}><span style="width:${percent}%"></span></div><small>${known ? percent.toFixed(1) + '%' : '尚未核实'}</small></div>`;
}

export function renderStatus(s, busy = false, readError = '', detailsOpen = false) {
  const view = statusModel(s), data = s.monitoring_verified === false ? {} : s.refresh || {}, analysis = s.monitoring_verified === false ? {} : s.analysis || {};
  const cell = (label, value, sub, cls = '', meter = '') => `<div class="mr-kpi ${cls}"><span>${label}</span><strong>${value}</strong><small>${escapeHtml(sub)}</small>${meter}</div>`;
  const fraction = (value, expected) => `${count(value)}<em> / ${count(expected)}</em>`;
  const target = s.data_target_15m;
  const buy = s.pending_publish ? '—' : count(s.latest_day_buy_count);
  const notice = readError || (view.stale ? '后台状态超过2分钟未发布，以下为上次有效数据。' : s.blocking_message || s.error || s.publisher_error);
  const total = s.monitoring_verified !== false && Number.isFinite(s.monitored_stock_count) && Number.isFinite(s.monitored_etf_count) ? s.monitored_stock_count + s.monitored_etf_count : undefined;
  const c = s.exclusive || {}, f = s.factor_retry || {};
  const retry = [f.next_eligible,s.next_market_retry].filter(v=>Number.isFinite(v)&&v>0).sort((a,b)=>a-b)[0];
  const next = f.due > 0 ? '待调度' : retry ? new Date(retry*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '无待定重试';
  const group = (name, fields) => `<section><h3>${name}</h3><dl>${fields.map(([k,v])=>`<div><dt>${k}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')}</dl></section>`;
  return `<header class="mr-header"><div class="mr-heading"><div class="mr-title"><h1>MA55 选股</h1><span class="mr-subtitle">原生15m · 人工复核候选</span><span class="mr-badge mr-tone-${view.tone}" role="status">${escapeHtml(readError ? '展示读取失败' : view.label)}</span></div></div>
    <button data-action="refresh" title="仅读取最新已发布状态，不触发行情采集" ${busy ? 'disabled' : ''}>${busy ? '读取中…' : '刷新展示'}</button></header>
    <section class="mr-kpis" aria-label="MA55扫描状态">
      ${cell('监控范围', count(total), `股票 ${count(s.monitored_stock_count)} · ETF ${count(s.monitored_etf_count)}`, 'mr-kpi-total')}
      ${cell('最新已完成15m', escapeHtml(target ? target.slice(11,16) : '—'), target ? target.slice(0,10)+' · 北京时间' : '交易日尚未核实', 'mr-kpi-target')}
      ${cell('当日K覆盖', fraction(data.refreshed,data.expected), '目标桶已保存', 'mr-kpi-quote', progress('K线覆盖',data.refreshed,data.expected))}
      ${cell('可分析候选', fraction(analysis.calculated,analysis.expected), '目标桶实际分析完成', 'mr-kpi-analysis', progress('MA55分析',analysis.calculated,analysis.expected))}
      ${cell('当日候选数', buy, `${s.latest_buy_date || '尚未核实'} · 新增 ${count((s.today_candidate_modes?.LIVE||0)+(s.today_candidate_modes?.DELAYED||0))} / 重放 ${count(s.today_candidate_modes?.REPLAY||0)}`, 'mr-kpi-buy')}
    </section>
    <section class="mr-runtime mr-tone-${view.tone}" aria-label="运行与数据状态">
      <div class="mr-runtime-main"><span class="mr-runtime-icon" aria-hidden="true">◉</span><div><strong>${escapeHtml(view.operation)}</strong><p>K已到但因子缺失 ${count(c.factor_missing)} · 预热不足 ${count(c.warmup)} · 源缺目标K ${count(c.missing_target)}</p><p>深历史使用余量，不阻挡当日候选</p></div></div>
      <div class="mr-runtime-end"><span>最近A/B成功 ${escapeHtml(s.last_success_live?.at?.slice(11,19) || '尚未核实')}</span><br><span>下一重试 ${escapeHtml(next)}</span></div>
      <details class="mr-runtime-detail" ${detailsOpen ? 'open' : ''}><summary>运行诊断</summary><div class="mr-diagnostic-groups">
        ${group('行情',[['目标',at(target)],['最新保存',at(s.latest_saved_15m)],['最近A/B成功',at(s.last_success_live?.at)],['源缺目标K',count(c.missing_target)],['行情等待重试',count(s.waiting_retry)],['下一行情重试',s.next_market_retry ? new Date(s.next_market_retry*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'无待定重试']])}
        ${group('因子',[['今日已核实',count(s.factors_verified_today)],['待补取',count(f.pending)],['可重试',count(f.due)],['冷却中',count(f.cooling)],['最近因子核实',at(f.last_verified)],['统计口径','因子与行情重试有重叠，不相加']])}
        ${group('候选',[['已分析',count(analysis.calculated)],['最低预热不足',count(c.warmup)],['输入待修复',count(c.invalid_input)],['最近计算',at(s.latest_analyzed_15m)],['最近发布',at(s.last_publish_at)],['最低输入','56根连续合法15m与已核实因子']])}
        ${group('历史',[['1280根库存与因子就绪',count(s.history_233_ready)],['剩余历史队列',count(s.history_queue_count)],['最近C成功',at(s.last_success_history?.at)],['历史用途','图表增强；各周期MA233按实际窗口显示'],['采集总预算',count(s.minute_request_limit)+' / 分钟'],['源端429',count(s.requests?.http_429_count)]])}
      </div><details class="mr-engineering"><summary>工程原始状态</summary><pre>${escapeHtml(JSON.stringify(s,null,2))}</pre></details></details>
      ${notice ? `<p class="mr-notice" role="status">${escapeHtml(notice)}</p>` : ''}
    </section>`;
}
