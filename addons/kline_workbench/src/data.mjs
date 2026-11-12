// All page IO uses the public Page SDK; saved bars and MAs come from lan.ma55.
import { epoch, stamp } from './aggregate.mjs';
export const DEMAND = 'lan_kline_demand', VIEW = 'lan_kline_view', SLOTS = 16, LEASE_MS = 90000;
export const fullSymbol = text => /^\d{6}\.(SH|SZ|BJ)$/.test(text.trim().toUpperCase()) ? text.trim().toUpperCase() : null;
export async function searchInstruments(api, query) {
  if (!query.trim()) throw Error('请输入名称或完整代码，例如600519.SH');
  const data = await api.instrumentSearch(query.trim().toUpperCase(), 20, 'stock,etf');
  return data.results.filter(r => ['stock','etf'].includes(r.asset_type) && fullSymbol(r.symbol));
}
export function decodePayload(payload, status) {
  return { ...payload, status, series:payload.series || {}, readAt:new Date().toISOString() };
}
export function savedSeries(data, period) {
  const rows=data?.series?.[period] || [], lookup=new Map(rows.map(r=>[r.timestamp,r]));
  const first=rows[0]?.timestamp, last=rows.at(-1)?.timestamp, size=Number(period.slice(0,-1)), bars=[];
  // Whitespace exposes missing saved buckets; no OHLC or MA is aggregated here.
  for (const day of data?.calendar || []) if (day>=first?.slice(0,10) && day<=last?.slice(0,10)) {
    for (const base of [570,780]) for (let end=base+size;end<=base+120;end+=size) {
      const timestamp=stamp(day,end);
      if (timestamp<first || timestamp>last) continue;
      const row=lookup.get(timestamp);
      bars.push(row?{...row,time:epoch(timestamp),complete:row.complete===true}:
        {timestamp,time:epoch(timestamp),complete:false,reason:'公共行情缺口'});
    }
  }
  let continuous=0;
  for (const b of bars) continuous=b.complete?continuous+1:0;
  return {bars,continuous,validCount:rows.length,gaps:bars.filter(b=>!b.complete).length,
    dataAsOf:last,dates:[...new Set(rows.map(r=>r.timestamp.slice(0,10)))]};
}
export function savedAverages(bars, periods, timeframe='15m') {
  return new Map(periods.map(n=>{
    const points=bars.map(b=>Number.isFinite(b['ma'+n]) && b.complete?
      {time:b.time,value:b['ma'+n]}:{time:b.time});
    // LWC skips whitespace when joining line points. Color the outgoing segment
    // transparent only at missing/invalid trading slots from savedSeries().
    // Adjacent exchange slots remain adjacent through lunch and closed days.
    for(let i=0;i<points.length-1;i++) if(points[i].value!==undefined &&
      points[i+1].value===undefined) points[i].color='transparent';
    return [n,points];
  }));
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
    const status=JSON.parse(row.status_json);
    if (payload.unavailable) return {symbol:selected.symbol,unavailable:true,status};
    return decodePayload(payload, status);
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
