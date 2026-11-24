export const WORKBENCH = 'lan_ma55_sector_workbench_v1';
export const COMMAND = 'lan_ma55_sector_command_v1';
export const RECEIPT = 'lan_ma55_sector_receipt_v1';
const SLOTS = 16, COMMAND_ROWS = 4 * SLOTS, LEASE_MS = 90000;

export const TIMEFRAMES = ['15m', '30m', '60m', '120m', 'daily', 'weekly'];
export const PERIOD_LABEL = { '15m': '15m', '30m': '30m', '60m': '60m', '120m': '120m', daily: '日K', weekly: '周K' };
export const SYMBOL = /^\d{6}\.(SH|SZ|BJ)$/;

const MESSAGES = {
  SCOPE_CONFLICT: '关注池已更新，请重读后保存',
  SCOPE_PAUSED: '等待保存关注池',
  CALENDAR_PENDING: '交易日历待核实',
  METADATA_PENDING: '标的类型或证券元数据待核实',
  QUOTA_WAIT: '等待行情请求额度',
  TICK_UNAVAILABLE: '最小报价单位待核实',
  FACTOR_PENDING: '复权因子待核实',
  INSUFFICIENT_HISTORY: '完成历史不足',
  MISSING_COMPLETED_BAR: '目标 K 线尚未到达，或未取得有效成交',
  GAP_IN_REPLAY_WINDOW: '回放窗口存在缺口或停牌',
  WAITING_INPUT: '等待有效数据',
  WAITING_FOR_COMPLETED_TARGET: '等待最新完成周期',
  WAITING_NEXT_15M: '等待下一根完成 K 线',
  OUTSIDE_FOCUS: '该证券已移出关注范围',
  HOST_BUSY: '宿主正在同步数据',
  THS_CATALOG_PENDING: '同花顺目录待核实',
  THS_MAPPING_PENDING: '等待同花顺完整成分库',
  THS_BOARD_UNAVAILABLE: '板块当前缺少可用成分快照',
  THS_STOCK_OUTSIDE_UNIVERSE: '股票未纳入当前 A 股证券池',
  THS_UNIVERSE_DEFERRED: '证券主目录等待核实',
  THS_CODE_3002: '同花顺正在准备板块关系，已延后重查',
  THS_MAPPING_UNVERIFIED_ValueError: '分类数据未通过核实',
  INVALID_PREFERENCES: '组或别名包含无效板块',
  MARKET_WIDE_SELECTION: '请缩小关注范围，全市场仅更新分类库',
  THS_KEY_REQUIRED: '请配置 THS_API_TOKEN',
  THS_KEY_REJECTED: '同花顺 Key 未获授权',
  THS_HTTP_401: '同花顺认证失败',
  THS_HTTP_403: '同花顺接口未授权',
  THS_RATE_LIMITED: '同花顺要求暂缓请求',
  THS_LOCAL_BUDGET: '等待同花顺本地请求额度',
  THS_MEMBER_UNVERIFIED: '板块成分未核实',
  THS_UNREACHABLE: '同花顺暂不可达',
  THS_REFRESH_COOLDOWN: '成分刷新间隔为 5 分钟',
  THS_SOURCE_TIME_UNVERIFIED: '同花顺源时间未通过核实',
  CREDENTIAL_REQUIRED: 'TickFlow 行情凭据不可用',
  SOURCE_UNAVAILABLE: '行情源暂不可用',
  RATE_LIMITED: '行情源要求暂缓请求',
  LEGACY_PERIOD_UNAVAILABLE: '此历史来源只有 15/30/60 分钟 K 线',
};

export const statusText = status => MESSAGES[status] || status || '等待工作台就绪';
export const normalizedTime = value => typeof value === 'string' ? value.replace('T', ' ').slice(0, 19) : '';

export function deepLink(search) {
  const query = new URLSearchParams(search);
  const symbol = (query.get('symbol') || '').toUpperCase();
  if (!SYMBOL.test(symbol)) return null;
  const rawSource = query.get('source') || '';
  const source = ['archive', 'TICKFLOW_RAW_5M', 'QUANTDASH_LEGACY', 'LEGACY_ARCHIVE'].includes(rawSource)
    ? 'archive' : ['native', 'TICKFLOW_NATIVE_15M'].includes(rawSource) ? 'native' : 'focus';
  const value = query.get('period');
  const periods = source === 'focus' ? TIMEFRAMES : TIMEFRAMES.slice(0, 3);
  return { symbol, source, timeframe: periods.includes(value) ? value : '15m',
    anchor: normalizedTime(query.get('event')) || null, eventId: query.get('event_id') || null };
}

export function createWorkbenchIO(api, {
  uuid = () => globalThis.crypto.randomUUID(), clock = Date.now,
  wait = ms => new Promise(resolve => setTimeout(resolve, ms)),
} = {}) {
  const client_id = uuid(), pendingWrites = new Map(), generations = new Map();
  let disposed = false;
  async function submit(kind, args) {
    const generation = (generations.get(kind) || 0) + 1;
    generations.set(kind, generation);
    const current = () => !disposed && generations.get(kind) === generation;
    const request_id = uuid();
    async function write() {
      for (let attempt = 0; attempt < 3 && current(); attempt++) {
        const { rows } = await api.extDataRows(COMMAND, { limit: COMMAND_ROWS });
        if (!current()) return null;
        const active = rows.filter(row => row.scope.startsWith(kind + '-') && clock() - row.request_ms < LEASE_MS);
        const taken = new Set(active.map(row => row.scope));
        const scope = active.find(row => row.client_id === client_id)?.scope
          || Array.from({ length: SLOTS }, (_, i) => kind + '-' + i).find(slot => !taken.has(slot));
        if (!scope) throw Error('工作台会话已满，请稍后重试');
        const request = { scope, client_id, request_id, request_ms: clock(), payload_json: JSON.stringify(args) };
        await api.extDataIngest(COMMAND, { rows: [request] });
        if (!current()) return null;
        const echo = (await api.extDataRows(COMMAND, { limit: COMMAND_ROWS })).rows.find(row => row.scope === scope);
        if (echo?.client_id === client_id && echo.request_id === request_id) return request;
      }
      if (current()) throw Error('工作台会话发生竞争，请重试');
      return null;
    }
    const sending = (pendingWrites.get(kind) || Promise.resolve()).catch(() => {}).then(write);
    pendingWrites.set(kind, sending);
    const sent = await sending;
    if (!sent) return null;
    while (current() && clock() - sent.request_ms < LEASE_MS) {
      const { rows } = await api.extDataRows(RECEIPT, { limit: COMMAND_ROWS });
      if (!current()) return null;
      const receipt = rows.find(row => row.scope === sent.scope && row.client_id === client_id
        && row.request_id === request_id && row.request_ms === sent.request_ms);
      if (receipt) {
        const result = JSON.parse(receipt.payload_json);
        if (result.ok === false) {
          const error = Error(statusText(result.error));
          error.focus = result.focus;
          throw error;
        }
        return result;
      }
      await wait(700);
    }
    if (current()) throw Error('后台尚未确认，请刷新后重试');
    return null;
  }
  return {
    async view() {
      const { rows } = await api.extDataRows(WORKBENCH, { limit: 1 });
      return rows[0]?.payload_json || null;
    },
    submit,
    dispose() { disposed = true; },
  };
}
