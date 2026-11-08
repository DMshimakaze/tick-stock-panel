// All page IO uses the public Page SDK. SQLite remains the backend authority.
export const DEMAND = 'lan_kline_demand', VIEW = 'lan_kline_view', SLOTS = 16, LEASE_MS = 90000;
export const fullSymbol = text => /^\d{6}\.(SH|SZ|BJ)$/.test(text.trim().toUpperCase()) ? text.trim().toUpperCase() : null;
export async function searchInstruments(api, query) {
  if (!query.trim()) throw Error('请输入名称或完整代码，例如600519.SH');
  const data = await api.instrumentSearch(query.trim().toUpperCase(), 20, 'stock,etf');
  return data.results.filter(r => ['stock','etf'].includes(r.asset_type) && fullSymbol(r.symbol));
}
export function decodePayload(payload, status) {
  const ratios = payload.ratios;
  const decode = (period) => (payload.raw?.[period] || []).map(([datetime,open,high,low,close,volume,amount]) => {
    const ratio = ratios === null ? 1 : ratios?.[datetime.slice(0,10)];
    if (!Number.isFinite(ratio) || ratio <= 0) throw Error('前复权投影缺少对应交易日，未显示混合价格');
    return { datetime, date:datetime.slice(0,10), open:open*ratio, high:high*ratio, low:low*ratio, close:close*ratio, volume, amount };
  });
  const group = rows => [...rows.reduce((map,r) => {
    if (!map.has(r.date)) map.set(r.date,[]);
    map.get(r.date).push(r); return map;
  },new Map())].map(([date,rows]) => ({date,rows}));
  return { ...payload, status, sessions:group(decode('5m')), oneSessions:group(decode('1m')), daily:decode('1d'), readAt:new Date().toISOString() };
}
export function createReader(api, { clientId = globalThis.crypto.randomUUID(), clock = Date.now } = {}) {
  let current = null, scope = null, generation = 0, lastDemand = 0, lastRevision = null, disposed = false;
  async function send(id) {
    for (let attempt=0; attempt<3; attempt++) {
      const rows = (await api.extDataRows(DEMAND,{limit:SLOTS})).rows;
      if (disposed || id !== generation) return;
      const own = rows.find(r => r.client_id===clientId && clock()-r.request_ms<LEASE_MS);
      const occupied = new Set(rows.filter(r => clock()-r.request_ms<LEASE_MS).map(r=>r.scope));
      scope = own?.scope ?? Array.from({length:SLOTS},(_,i)=>String(i)).find(s=>!occupied.has(s));
      if (scope === undefined) throw Error('16个图表会话正在使用，请关闭闲置图表后重试');
      const demand = {scope, client_id:clientId, requested_symbol:current.symbol, request_ms:clock(), request_id:current.id, period:current.period, pinned:current.pinned};
      await api.extDataIngest(DEMAND,{rows:[demand]});
      const receipt = (await api.extDataRows(DEMAND,{limit:SLOTS})).rows.find(r=>r.scope===scope);
      if (disposed || id !== generation) return;
      if (receipt?.client_id===clientId && receipt.request_id===current.id) { lastDemand=clock(); current.pinned=-1; return; }
    }
    throw Error('图表会话槽发生竞争，请刷新重试');
  }
  async function poll() {
    const id = generation, selected = current;
    if (!selected || disposed) return null;
    if (clock()-lastDemand>=30000) await send(id);
    const metadata = (await api.extDataRows(VIEW,{limit:SLOTS, columns:['scope','client_id','requested_symbol','request_id','revision','phase','error','status_json','asof']})).rows;
    if (disposed || id !== generation) return null;
    const matches = r => r.scope===scope && r.client_id===clientId && r.requested_symbol===selected.symbol && r.request_id===selected.id;
    const row = metadata.find(matches);
    if (!row) return {symbol:selected.symbol, pending:true, status:{phase:'WARMING'}};
    if (row.revision===lastRevision) return null;
    const result = (await api.extDataRows(VIEW,{limit:SLOTS, columns:['scope','client_id','requested_symbol','request_id','revision','payload_json']})).rows.find(matches);
    if (disposed || id !== generation || !result || result.revision!==row.revision) return null;
    const payload = JSON.parse(result.payload_json || '{}');
    if (payload.symbol !== selected.symbol) throw Error('发布视图标的不匹配，未渲染旧股票');
    lastRevision=row.revision;
    return decodePayload(payload, JSON.parse(row.status_json));
  }
  return {
    async read(symbol, {period='15m',pinned=null}={}) {
      if (!fullSymbol(symbol)) throw Error('需要完整股票代码');
      const id=++generation;
      current={symbol,period,pinned:pinned===null?-1:Number(pinned),id:clientId+':'+id}; lastRevision=null;
      await send(id);
      return id===generation ? poll() : null;
    }, poll,
    dispose() { disposed=true; generation++; current=null; },
  };
}
