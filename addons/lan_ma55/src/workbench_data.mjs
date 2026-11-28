export const WORKBENCH = 'lan_ma55_sector_workbench_v1';
export const RUNTIME = 'lan_ma55_sector_runtime_v1';
export const COMMAND = 'lan_ma55_sector_command_v1';
export const RECEIPT = 'lan_ma55_sector_receipt_v1';
const SLOTS = 16, COMMAND_ROWS = 4 * SLOTS, LEASE_MS = 90000;

export const TIMEFRAMES = ['15m', '30m', '60m', '120m', 'daily', 'weekly'];
export const PERIOD_LABEL = { '15m': '15m', '30m': '30m', '60m': '60m', '120m': '120m', daily: '日K', weekly: '周K' };
export const SYMBOL = /^\d{6}\.(SH|SZ|BJ)$/;

const MESSAGES = {
  SCOPE_CONFLICT: '另一窗口已保存 · 重新预览',
  FOCUS_PREVIEW_CHANGED: '成分已更新 · 请确认最新差集',
  SCOPE_PAUSED: '等待保存关注池',
  CALENDAR_PENDING: '交易日历待核实',
  METADATA_PENDING: '标的类型或证券元数据待核实',
  QUOTA_WAIT: '等待行情请求额度',
  TICK_UNAVAILABLE: '最小报价单位待核实',
  FACTOR_PENDING: '复权因子待核实',
  INSUFFICIENT_HISTORY: '完成历史不足',
  MISSING_COMPLETED_BAR: '目标 K 线尚未到达，或未取得有效成交',
  GAP_IN_REPLAY_WINDOW: '回放窗口存在缺柱',
  ZERO_VOLUME_BAR: '回放窗口含零成交柱',
  TRADING_HALTED_VERIFIED: '交易所公告停牌',
  WAITING_INPUT: '等待有效数据',
  WAITING_FOR_COMPLETED_TARGET: '等待最新完成周期',
  WAITING_FOR_REVIEW: '输入已变更，等待复核',
  WAITING_NEXT_15M: '等待下一根完成 K 线',
  OUTSIDE_FOCUS: '该证券已移出关注范围',
  HOST_BUSY: '宿主正在同步数据',
  DC_MAPPING_PENDING: '等待东财完整成分库',
  DC_BOARD_UNAVAILABLE: '板块当前缺少可用成分快照',
  DC_STOCK_OUTSIDE_UNIVERSE: '股票未纳入当前 A 股证券池',
  DC_UNIVERSE_DEFERRED: '证券主目录等待核实',
  DC_UNIVERSE_REQUIRED: '等待证券主目录复核',
  DC_UNIVERSE_CHANGED: '证券池有变，已安排重建',
  DC_MIGRATION_CONFIRM: '请预览并确认旧关注迁移',
  DC_INDUSTRY_AWAITING_TREE: '行业有变化，等待完整路径核实',
  DC_SCOPE_OUTDATED: '部分旧关注已不在当前证券范围',
  INVALID_PREFERENCES: '组或别名包含无效板块',
  MARKET_WIDE_SELECTION: '请缩小关注范围，全市场仅更新分类库',
  DC_GATEWAY_REQUIRED: '东财网关凭据未配置',
  DC_AUTHORIZATION: '东财请求认证未通过',
  DC_RATE_LIMIT: '东财网关要求暂缓请求',
  DC_LOCAL_BUDGET: '等待东财本地请求额度',
  DC_NETWORK: '东财网关暂不可达',
  DC_TIMEOUT: '东财请求超时',
  DC_MEMBER_PAGE_LIMIT: '东财成分触及行数上限，重新分批',
  DC_MISSING_BATCH_GROUP: '本批次缺少板块，重新分批',
  DC_NO_DATED_MEMBERS: '暂未取得带来源日期的成分',
  CREDENTIAL_REQUIRED: 'TickFlow 行情凭据不可用',
  SOURCE_UNAVAILABLE: '行情源暂不可用',
  SOURCE_SYMBOL_OMITTED: '行情响应未返回该证券',
  SOURCE_SYMBOL_NULL: '该证券的行情响应为 null',
  MISSING_RESPONSE: '未取得可核实的因子响应',
  FACTOR_KEY_MISSING: '因子响应缺少该证券',
  FACTOR_NULL_RESPONSE: '该证券的因子响应为 null',
  INVALID_FACTOR_ENVELOPE: '因子响应格式无效',
  INVALID_FACTOR_SCHEMA: '该证券的因子列表格式无效',
  INVALID_FACTOR_EVENT: '因子日期或数值无效',
  CONFLICTING_FACTOR_EVENTS: '同日因子数值冲突',
  INVALID_NATIVE15_OHLCV: '原生 15m OHLCV 未通过校验',
  INVALID_NATIVE15_SCHEMA: '原生 15m 响应格式无效',
  NO_NEW_NATIVE15: '本次未新增有效原生柱',
  RATE_LIMITED: '行情源要求暂缓请求',
  ERROR: '后台处理异常',
  LEGACY_PERIOD_UNAVAILABLE: '此历史来源只有 15/30/60 分钟 K 线',
};

export const statusText = status => MESSAGES[status] || status || '等待工作台就绪';
export const normalizedTime = value => typeof value === 'string' ? value.replace('T', ' ').slice(0, 19) : '';
export const currentFocus = (incoming, current) => !current || incoming.revision > current.revision ||
  (incoming.revision === current.revision && (incoming.asof_us ?? 0) >= (current.asof_us ?? 0));

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
  locks = globalThis.navigator?.locks,
} = {}) {
  const client_id = uuid(), generations = new Map();
  let pendingWrite = Promise.resolve();
  let disposed = false;
  async function readRows(table, limit) {
    // Ext Data readers can briefly see a Parquet footer during an official ingest.
    for (const delay of [80, 200, 0]) {
      try {
        return await api.extDataRows(table, { limit });
      } catch (error) {
        if (disposed || error.status !== 500 || !delay) throw error;
        await wait(delay);
      }
    }
  }
  async function submit(kind, args) {
    const generation = (generations.get(kind) || 0) + 1;
    generations.set(kind, generation);
    const current = () => !disposed && generations.get(kind) === generation;
    const request_id = uuid();
    async function write() {
      for (let attempt = 0; attempt < 3 && current(); attempt++) {
        const { rows } = await readRows(COMMAND, COMMAND_ROWS);
        if (!current()) return null;
        const active = rows.filter(row => row.scope.startsWith(kind + '-') && clock() - row.request_ms < LEASE_MS);
        const taken = new Set(active.map(row => row.scope));
        const scope = active.find(row => row.client_id === client_id)?.scope
          || Array.from({ length: SLOTS }, (_, i) => kind + '-' + i).find(slot => !taken.has(slot));
        if (!scope) throw Error('工作台会话已满，请稍后重试');
        const request = { scope, client_id, request_id, request_ms: clock(), payload_json: JSON.stringify(args) };
        await api.extDataIngest(COMMAND, { rows: [request] });
        if (!current()) return null;
        const echo = (await readRows(COMMAND, COMMAND_ROWS)).rows.find(row => row.scope === scope);
        if (echo?.client_id === client_id && echo.request_id === request_id) return request;
      }
      if (current()) throw Error('工作台会话发生竞争，请重试');
      return null;
    }
    // TSP snapshot ingests share one temporary path per table. Serialize the
    // read/merge/echo across this origin's tabs, then release it before polling.
    const sending = pendingWrite.catch(() => {}).then(() =>
      locks ? locks.request(COMMAND, write) : write());
    pendingWrite = sending;
    const sent = await sending;
    if (!sent) return null;
    while (current() && clock() - sent.request_ms < LEASE_MS) {
      const { rows } = await readRows(RECEIPT, COMMAND_ROWS);
      if (!current()) return null;
      const receipt = rows.find(row => row.scope === sent.scope && row.client_id === client_id
        && row.request_id === request_id && row.request_ms === sent.request_ms);
      if (receipt) {
        const result = JSON.parse(receipt.payload_json);
        if (result.ok === false) {
          const error = Error(statusText(result.error));
          error.focus = result.focus;
          error.code = result.error;
          error.preview = result.preview;
          error.unavailable = result.unavailable;
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
      const { rows } = await readRows(WORKBENCH, 1);
      return rows[0]?.payload_json || null;
    },
    async runtime() {
      const { rows } = await readRows(RUNTIME, 1);
      return rows[0]?.payload_json ? JSON.parse(rows[0].payload_json) : null;
    },
    submit,
    dispose() { disposed = true; },
  };
}
