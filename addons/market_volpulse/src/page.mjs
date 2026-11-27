import { CSS } from './styles.mjs';
import { bindHover, charts, finite } from './chart.mjs';
import { bridge, requestId } from './bridge.mjs';
import { settingsDialog } from './settings.mjs';

const MARKETS = [
  ['MARKET_TOTAL', '沪深两市', '沪市＋深市 · 不含北交所'],
  ['000001.SH', '上证指数', '沪市全市场成交额'],
  ['399001.SZ', '深证成指', '行情源的深市全市场成交额'],
  ['399006.SZ', '创业板指', '按所示来源的创业板统计口径'],
  ['000680.SH', '科创综指', '按所示来源的科创综指成交额'],
];
const sourceName = { tencent: '腾讯财经', eastmoney: '东方财富', sina: '新浪财经' };
const states = { EXPANDING: '放量', FLAT: '平量', SHRINKING: '缩量', UNAVAILABLE: '待数据' };
const phases = {
  AUCTION: '开盘竞价', LUNCH: '午休', POST_CLOSE: '已收盘', CLOSED_DAY: '休市', PREOPEN: '盘前',
  HISTORICAL: '历史', CALENDAR_UNVERIFIED: '日历待确认',
};
const safe = v => String(v ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
const amount = (v, decimal = 0) => finite(v) ? (v / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: decimal }) : '—';
const signed = (v, n = 2) => finite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(n)}` : '—';
const pct = v => finite(v) ? `${signed(v)}%` : '—';
const diff = v => finite(v) ? `${v > 0 ? '+' : ''}${amount(v, 1)} 亿` : '—';
const multiple = v => finite(v) ? `${v.toFixed(2)}×` : '—';
const tone = v => !finite(v) || v === 0 ? '' : v > 0 ? 'vp-up' : 'vp-down';
const shortStamp = v => typeof v === 'string' ? v.replace('T', ' ').slice(0, 19) : '—';
const sourceLabel = id => Object.hasOwn(sourceName, id) ? sourceName[id] : '待连接';
const directionLabel = direction => direction === 'SURGE' ? '急放量' : '急缩量';

function validView(value) {
  return value?.schema_version === 1 && Array.isArray(value.indices) && Array.isArray(value.events) &&
    value.indices.every(row => row?.metrics && Array.isArray(row.points));
}

function metric(label, value, unit, detail, cls = '', primary = false) {
  return `<div class="vp-card ${primary ? 'hero-main' : ''}">
    <div class="vp-label">${label}</div><div class="vp-number ${cls}">${value}${unit ? `<span class="vp-unit">${unit}</span>` : ''}</div>
    <div class="vp-small">${detail}</div></div>`;
}

function predictionDetail(metrics) {
  if (!finite(metrics.predicted_amount)) return '等待同源历史基线';
  if (metrics.prediction_basis === 'close') return '收盘成交额';
  if (metrics.prediction_basis === 'previous') return '上一交易日模板 · 1 日';
  return `${safe(metrics.sample_days)} 个完整交易日`;
}

function header(latest, row, status, selectedDay, command) {
  const dates = latest?.dates ?? [];
  return `<header class="vp-head">
    <div><h1>大盘量能监控</h1><p class="vp-sub">沪深两市成交额与上一交易日同期对照</p></div>
    <div class="vp-status"><span class="vp-badge"><i class="vp-dot ${status === '实时' ? 'live' : ''}"></i>${safe(status)}</span>
      <span>${safe(shortStamp(row?.stamp))} 北京时间</span>
      <label class="vp-date"><span class="vp-sr">交易日期</span>
        <select data-select="day" aria-label="交易日期" ${dates.length && !command ? '' : 'disabled'}>
          <option value="">最新交易日${latest?.trade_date ? ` · ${safe(latest.trade_date)}` : ''}</option>
          ${dates.map(day => `<option value="${safe(day)}" ${day === selectedDay ? 'selected' : ''}>${safe(day)}</option>`).join('')}
        </select>
      </label>
      <button type="button" data-action="refresh" ${command ? 'disabled' : ''}>刷新</button>
      <button type="button" data-action="settings" ${latest?.settings && !command ? '' : 'disabled'}>设置</button>
    </div>
  </header>`;
}

function eventList(events, direction, selectedEvent) {
  return `<div class="vp-section-head"><h2>量能异动</h2>
    <select aria-label="异动方向" data-select="direction">
      <option value="">全部方向</option><option value="SURGE" ${direction === 'SURGE' ? 'selected' : ''}>急放量</option>
      <option value="SHRINK" ${direction === 'SHRINK' ? 'selected' : ''}>急缩量</option>
    </select></div>
    <div class="vp-small">${events.length} 条 · 时间倒序</div>
    <div class="vp-event-list">${events.length ? events.map(e => `<button type="button" class="vp-event" data-k="${safe(e.k)}"
      aria-pressed="${selectedEvent === e.k}" ${finite(e.k) ? '' : 'disabled'}>
      <strong class="${e.direction === 'SURGE' ? 'vp-up' : 'vp-down'}">${directionLabel(e.direction)}</strong>
      <span class="vp-event-time">${safe(shortStamp(e.stamp).slice(11))}</span>
      <span class="vp-event-metrics">${multiple(e.multiple)} · ${signed(e.slope)} pp/min</span></button>`).join('') :
    '<div class="vp-empty">当前无已记录异动</div>'}</div>`;
}

function details(row, view, window) {
  const settings = view?.settings ?? {};
  const formatAuction = value => finite(value) ? `${amount(value, 1)} 亿` : '—';
  return `<div class="vp-section-head"><h2>数据与判定</h2></div>
    <div class="vp-rule-row"><span>分时基线</span><strong>${safe(sourceLabel(row?.source))}</strong></div>
    <div class="vp-rule-row"><span>09:25 集合竞价 · 今 / 昨</span><strong>${formatAuction(row?.auction?.today)} / ${formatAuction(row?.auction?.previous)}</strong></div>
    <div class="vp-rule-row"><span>闭合分钟</span><strong>${safe(row?.covered_minutes ?? '—')} / ${safe(row?.expected_minutes ?? '—')}</strong></div>
    <div class="vp-rule-row"><span>累计放量 / 缩量</span><strong>${pct(settings.expand_pct)} / ${pct(settings.shrink_pct)}</strong></div>
    <div class="vp-rule-row"><span>急放量</span><strong class="vp-up">${multiple(settings.surge_multiple)} · ${signed(settings.surge_slope)} pp/min</strong></div>
    <div class="vp-rule-row"><span>急缩量</span><strong class="vp-down">${multiple(settings.shrink_multiple)} · ${signed(settings.shrink_slope)} pp/min</strong></div>
    <p class="vp-small vp-rule-foot">${window} 分钟沿交易时轴滚动，午休折叠。成交额用于量能观察。</p>`;
}

export function mountPage(el, ctx, { read, preview = false } = {}) {
  const api = read ? { view: read } : bridge(ctx.api);
  const root = document.createElement('section');
  root.className = 'vp';
  const content = document.createElement('div');
  const liveMessage = document.createElement('div');
  liveMessage.className = 'vp-toast';
  liveMessage.setAttribute('role', 'status');
  liveMessage.hidden = true;
  const style = document.createElement('style');
  style.textContent = CSS;
  root.append(content, liveMessage);
  el.append(style, root);

  let disposed = false;
  let busy = false;
  let timer;
  let toastTimer;
  let latest = null;
  let failure = null;
  let selected = 'MARKET_TOTAL';
  let selectedDay = '';
  let historical = null;
  let direction = '';
  let hover = null;
  let focusMinute = null;
  let locate = () => {};
  let command = null;
  let seenEvent = null;
  const historyCache = new Map();
  const lockWait = new AbortController();
  const settings = settingsDialog(root, values =>
    send('settings', { values, revision: latest.settings_revision }));

  function render() {
    const view = selectedDay ? historical : latest;
    const row = view?.indices.find(item => item.symbol === selected);
    const metrics = row?.metrics ?? {};
    const phase = view?.phase || (preview ? 'LIVE' : '');
    const window = view?.window ?? latest?.settings?.window ?? 5;
    const updating = view?.mode === 'LIVE' && !preview &&
      (!Number.isFinite(Date.parse(view.generated_at)) || Date.now() - Date.parse(view.generated_at) > 45_000);
    const stale = updating || (Boolean(failure) && view?.mode === 'LIVE');
    const status = preview ? '界面预览' : selectedDay ? '历史' : stale ? '已陈旧' :
      phases[phase] || (row?.status === 'LIVE' ? '实时' :
        row?.status === 'DEGRADED' ? '数据降级' : '待初始化');
    const market = MARKETS.find(item => item[0] === selected) ?? MARKETS[0];
    const rowEvents = (view?.events ?? []).filter(e => e?.symbol === selected);
    const events = direction ? rowEvents.filter(event => event.direction === direction) : rowEvents;
    const [upper, lower] = charts(row?.points, selected !== 'MARKET_TOTAL', {
      auction: row?.auction, events: rowEvents, snapshot: stale ? null : row?.snapshot,
    });
    const historyBusy = selectedDay && command?.kind === 'history';
    const notice = preview ? '预览数据，仅用于检查交互' : failure || (stale ? '后台更新中断，显示上次发布' :
      historyBusy ? '正在读取本地历史' : view?.runtime?.calendar_error || row?.reason ||
      (!view ? '等待后台发布量能数据' : null));
    const marketDateLabel = view?.trade_date ? `${safe(view.trade_date)} / ${safe(view.previous_date ?? '—')}` : '等待交易日';
    const quoteLabel = row?.quote_source ? ` · 快照 ${sourceLabel(row.quote_source)}` : '';
    const dayAmount = phase === 'AUCTION' ? row?.auction?.today : metrics.today_amount;
    const amountDetail = phase === 'AUCTION' ? '09:25 集合竞价终态' :
      `${safe(states[metrics.state] || '待数据')} · 同期 <strong class="${tone(metrics.ratio_pct)}">${pct(metrics.ratio_pct)} / ${diff(metrics.diff)}</strong>`;
    content.innerHTML = `${header(latest, row, status, selectedDay, command)}
      ${notice ? `<div class="vp-notice" role="status">${safe(notice)}</div>` : ''}
      <div class="vp-hero">
        ${metric('累计成交额', amount(dayAmount), '亿元', amountDetail, '', true)}
        ${metric(metrics.prediction_basis === 'close' ? '全天成交额' : '全天成交额预测', amount(metrics.predicted_amount),
          '亿元', predictionDetail(metrics))}
        ${metric(`近 ${safe(window)} 分钟边际量比`, multiple(metrics.multiple), '', '与上一交易日同区间相比', tone(finite(metrics.multiple) ? metrics.multiple - 1 : null))}
        ${metric('边际变化斜率', signed(metrics.slope), 'pp/min', `边际偏离 ${pct(metrics.deviation_pct)}`, tone(metrics.slope))}
      </div>
      <div class="vp-index-grid">
        ${MARKETS.map(([symbol, name, scope]) => {
          const item = view?.indices.find(r => r.symbol === symbol);
          const m = item?.metrics;
          return `<button type="button" class="vp-index" data-symbol="${symbol}" title="${scope}" aria-pressed="${symbol === selected}">
            <span class="vp-index-top"><span class="vp-index-name">${name}</span>
              <span class="vp-index-num ${tone(item?.change_pct)}">${symbol === 'MARKET_TOTAL' ? safe(states[m?.state] || '—') : pct(item?.change_pct)}</span></span>
            <span class="vp-index-bottom"><span>${amount(phase === 'AUCTION' ? item?.auction?.today : m?.today_amount)} 亿</span>
              <span class="${tone(m?.ratio_pct)}">${pct(m?.ratio_pct)}</span></span>
          </button>`;
        }).join('')}
      </div>
      <div class="vp-layout">
        <section class="vp-panel">
          <div class="vp-section-head"><div><h2 class="vp-chart-title">${market[1]} · 分时成交额</h2>
            <div class="vp-small">${marketDateLabel} · ${safe(sourceLabel(row?.source) + quoteLabel)}</div></div>
            <div class="vp-small vp-scope">${market[2]} ${finite(row?.price) ? ` · ${row.price.toFixed(2)} 点` : ''}</div>
          </div>
          <div class="vp-legend"><span class="vp-up"><i class="vp-key"></i>今日累计</span>
            <span><i class="vp-key dash"></i>上一交易日累计</span>
            ${selected !== 'MARKET_TOTAL' ? '<span class="vp-price-key"><i class="vp-key"></i>指数点位 · 右轴</span>' : ''}
            ${row?.snapshot && !stale ? '<span class="vp-up"><i class="vp-key dash"></i>未闭合快照</span>' : ''}
          </div>
          <div class="vp-chart-wrap"><div class="vp-hover" hidden></div>${upper}
            <div class="vp-divider"></div>
            <div class="vp-section-head"><h2>分钟成交额与边际量比</h2><span class="vp-small">量比右轴 · 1.0× 参考线</span></div>
            <div class="vp-legend"><span class="vp-up"><i class="vp-key"></i>放量分钟</span>
              <span class="vp-down"><i class="vp-key"></i>缩量分钟</span><span><i class="vp-key"></i>上一交易日分钟</span>
              <span class="vp-ratio-key"><i class="vp-key"></i>边际量比</span></div>${lower}
          </div>
        </section>
        <aside><section class="vp-panel">${eventList(events, direction, focusMinute)}</section>
          <section class="vp-panel vp-rule">${details(row, view, window)}</section></aside>
      </div>
      <footer class="vp-foot"><span>ashare-volpulse v0.1.5 · MIT</span>
        <span>${view?.runtime?.calendar ? `沪深交易日历 ${safe(view.runtime.calendar)} · ` : ''}北京时间</span></footer>`;
    if (row) {
      locate = bindHover(content, stale ? { ...row, snapshot: null } : row, key => { hover = key; });
      locate(hover ?? focusMinute);
    }
  }

  function notify(view) {
    const events = view.events.filter(e => Number.isInteger(e?.id));
    const last = Math.max(0, ...events.map(e => e.id));
    if (seenEvent !== null && view.mode === 'LIVE' && !selectedDay && view.settings?.notifications) {
      const event = events.find(e => e.id > seenEvent && Date.now() - Date.parse(e.stamp) < 30_000);
      if (event) {
        const name = MARKETS.find(([symbol]) => event.symbol === symbol)?.[1] ?? '';
        liveMessage.textContent = `${name} ${directionLabel(event.direction)} · ${multiple(event.multiple)}`;
        liveMessage.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { liveMessage.hidden = true; }, 5000);
      }
    }
    seenEvent = Math.max(seenEvent ?? 0, last);
  }

  function finish(reply) {
    const current = command;
    command = null;
    current?.release?.();
    if (!current || disposed) return;
    if (current.kind === 'settings') {
      settings.finish(reply.ok, reply.ok ? '后台已保存，设置已生效' : reply.error);
      if (reply.ok && latest) {
        latest = { ...latest, settings: reply.settings, settings_revision: reply.revision };
        historyCache.clear();
      }
    } else if (reply.ok && validView(reply.view) && reply.view.trade_date === selectedDay) {
      historical = reply.view;
      historyCache.set(selectedDay, reply.view);
    } else {
      failure = reply.error || '后台返回的历史日期不匹配';
    }
    render();
  }

  function queue(delay) {
    clearTimeout(timer);
    if (!disposed) timer = setTimeout(refresh, delay);
  }

  async function readView() {
    let next = await api.view();
    if (disposed || !next) return;
    if (!validView(next)) throw new Error('后台视图格式不匹配');
    if (latest?.settings_revision > next.settings_revision) {
      next = { ...next, settings: latest.settings, settings_revision: latest.settings_revision };
    }
    if (latest?.settings_revision !== next.settings_revision) historyCache.clear();
    const changed = next.generated_at !== latest?.generated_at || failure;
    latest = next;
    failure = null;
    notify(next);
    if (changed) render();
  }

  async function refresh() {
    if (disposed || busy) return;
    busy = true;
    clearTimeout(timer);
    try {
      if (command?.ready) {
        if (Date.now() >= command.expires) {
          finish({ ok: false, error: '后台回执超时，请刷新后重试' });
        } else if (!command.sent) {
          await api.send(command.payload);
          if (command) command.sent = true;
        } else {
          const reply = await api.receipt();
          if (command && reply?.request_id === command.payload.request_id) {
            if (command.kind === 'settings' && reply.ok) {
              await readView();
              if (disposed) return;
              finish(reply);
              if (selectedDay) {
                historical = null;
                send('history', { day: selectedDay });
              }
            } else {
              finish(reply);
            }
          }
        }
      } else if (!command) {
        await readView();
      }
    } catch (error) {
      if (command) finish({ ok: false, error: `请求失败：${error?.message || '读取失败'}` });
      else if (!disposed) {
        failure = `读取后台量能失败：${error?.message || '连接中断'}`;
        render();
      }
    } finally {
      busy = false;
      if (!preview && !disposed) {
        const liveInterval = (latest?.settings?.refresh_seconds ?? 5) * 1000;
        queue(command?.ready ? 900 : failure || latest?.mode !== 'LIVE' ? 6000 : liveInterval);
      }
    }
  }

  function send(kind, values) {
    if (command || disposed || preview) return false;
    command = { kind };
    const dispatch = () => new Promise(resolve => {
      if (disposed) return resolve();
      const now = Date.now();
      Object.assign(command, { ready: true, release: resolve, expires: now + 20_000,
        payload: { kind, request_id: requestId(), sent_at: now, ...values } });
      queue(0);
    });
    if (navigator.locks) {
      navigator.locks.request('market.volpulse.command', { signal: lockWait.signal }, dispatch)
        .catch(() => { if (!disposed) finish({ ok: false, error: '页面请求未能取得写入顺序' }); });
    } else {
      dispatch();
    }
    render();
    return true;
  }

  function click(event) {
    const button = event.target.closest('button');
    if (!button || !content.contains(button)) return;
    if (button.dataset.symbol) {
      selected = button.dataset.symbol;
      focusMinute = hover = null;
      render();
    } else if (button.dataset.k) {
      const minute = Number(button.dataset.k);
      focusMinute = focusMinute === minute ? null : minute;
      hover = null;
      render();
      locate(focusMinute);
    } else if (button.dataset.action === 'refresh') {
      if (selectedDay) {
        historical = null;
        failure = null;
        send('history', { day: selectedDay });
      } else {
        queue(0);
      }
    } else if (button.dataset.action === 'settings' && latest?.settings) {
      settings.open(latest.settings, Boolean(command));
    }
  }

  function change(event) {
    const select = event.target;
    if (select.dataset.select === 'direction') {
      direction = select.value;
      render();
    }
    if (select.dataset.select === 'day') {
      selectedDay = select.value;
      failure = null;
      focusMinute = hover = null;
      historical = historyCache.get(selectedDay) ?? null;
      if (selectedDay && !historical) send('history', { day: selectedDay });
      else render();
    }
  }

  content.addEventListener('click', click);
  content.addEventListener('change', change);
  render();
  refresh();
  return () => {
    disposed = true;
    lockWait.abort();
    command?.release?.();
    clearTimeout(timer);
    clearTimeout(toastTimer);
    content.removeEventListener('click', click);
    content.removeEventListener('change', change);
    root.remove();
    style.remove();
  };
}
