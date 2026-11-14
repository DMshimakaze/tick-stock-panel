export const INDUSTRY = 'ext_hy_ths';
export const FIELD = '所属同花顺行业';
export const FAMILY = 'NATIVE15_REVIEW_V1';
export const VERSION = 'FLAT_INDUSTRY_RANK_V1';
const fullSymbol = value => typeof value==='string' && /^\d{6}\.(SH|SZ|BJ)$/.test(value.trim().toUpperCase()) ? value.trim().toUpperCase() : null;
const mean = values => values.length ? values.reduce((a,b)=>a+b,0)/values.length : null;

/** The saved fanout flattened multiple catalog labels with '-'. Neither that
 * string nor slash/space-delimited words establish a hierarchy or industry ID. */
export function membershipIndex(data, config={}) {
  const sectors=new Map(),bySymbol=new Map(),pending=[],invalidSymbols=[];
  for(const row of data.rows) {
    const symbol=fullSymbol(row.symbol),raw_label=String(row[FIELD]??'').trim();
    if(!symbol) { invalidSymbols.push({symbol:row.symbol,raw_label});continue; }
    if(!raw_label || raw_label.includes('-')) {
      pending.push({symbol,name:row.name||row['股票简称']||'',raw_label,reason:raw_label?'AMBIGUOUS_JOIN':'MISSING_LABEL'});
      continue;
    }
    if(!sectors.has(raw_label))sectors.set(raw_label,new Map());
    sectors.get(raw_label).set(symbol,{symbol,name:row.name||row['股票简称']||'',raw_label});
    if(!bySymbol.has(symbol))bySymbol.set(symbol,[]);
    if(!bySymbol.get(symbol).includes(raw_label))bySymbol.get(symbol).push(raw_label);
  }
  const syncAt=config.latest_sync_date?.replace('T',' ')||null;
  return {sectors,bySymbol,pending,invalidSymbols,total:data.total,received:data.rows.length,
    complete:data.total===data.rows.length,source:config.id,description:config.description||'',syncAt,
    // This is an observed file-write version, not a vendor effective date.
    sourceAsOf:null,version:`${config.id}@${syncAt||'unknown'}|${config.updated_at||'unknown'}|${data.total}`};
}

export function scoreIndustries(rotation,membership,market,now,referenceDate=market?.as_of?.slice(0,10)) {
  const today=now.slice(0,10),closed=now.slice(11,19)>='15:00:00';
  const dates=[...new Set(rotation.dates)].filter(d=>d<today || d===today&&closed).sort().reverse().slice(0,20);
  const daily=new Map(),values=new Map();
  for(const date of dates) {
    const column=(rotation.columns[date]||[]).filter(([name,pct])=>membership.sectors.has(name)&&Number.isFinite(pct));
    column.sort((a,b)=>b[1]-a[1] || a[0].localeCompare(b[0],'zh-CN'));
    daily.set(date,new Map(column.map(([name,pct],i)=>[name,{rank:i+1,pct,
      percentile:column.length===1?1:1-i/(column.length-1),universe:column.length}])));
    values.set(date,column.length);
  }
  const asof=dates[0]||null,marketDay=market?.as_of?.slice(0,10);
  const referenceClosed=referenceDate && (referenceDate<today || referenceDate===today&&closed);
  const marketPcts=asof&&marketDay===asof ? (market.rows||[]).map(r=>r.change_pct).filter(Number.isFinite) : [];
  const benchmark={asof:marketDay||null,value:mean(marketPcts),coverage:marketPcts.length};
  const fresh=!!asof && !!membership.syncAt && membership.syncAt.slice(0,10)>=asof && membership.syncAt<=now;
  const rows=[];
  for(const [name,members] of membership.sectors) {
    const history=dates.map(date=>({date,...daily.get(date).get(name)}));
    const known=history.filter(r=>r.rank!==undefined),missingDates=history.filter(r=>r.rank===undefined).map(r=>r.date);
    const status=!membership.complete?'INCOMPLETE_MEMBERSHIP':!fresh?'STALE_MEMBERSHIP':
      dates.length!==20 || missingDates.length?'INSUFFICIENT_HISTORY':
      referenceClosed&&asof<referenceDate?'STALE_RPS':'OK';
    const recent=history.slice(0,5).filter(r=>r.rank!==undefined);
    const recent5=recent.length===5?mean(recent.map(r=>r.percentile)):null;
    const average20=known.length===20?mean(known.map(r=>r.percentile)):null;
    const delta=recent5!==null&&average20!==null?recent5-average20:null;
    const top10=known.filter(r=>r.rank<=10).length,top20=known.filter(r=>r.rank<=20).length;
    const valid=status==='OK',latest=history[0];
    const score=valid?Math.round(1000*(.7*top10/20+.3*recent5))/10:null;
    let category='观察';
    if(!valid)category='数据待核实';
    else if(delta>=.2&&recent5>=.6)category='新晋走强';
    else if(delta<=-.2&&recent5<=.4)category='明显退潮';
    else if(top10>=10&&recent5>=.6)category='持续强势';
    rows.push({name,members,valid,status,score,category,top10,top20,coverage:known.length/20,
      observedDays:known.length,missingDates,recent5,average20,delta,
      latestRank:latest?.rank??null,latestChange:latest?.pct??null,
      relativeMarket:latest?.pct!==undefined&&benchmark.value!==null?latest.pct-benchmark.value:null,
      history,membershipVersion:membership.version});
  }
  rows.sort((a,b)=>(b.score??-1)-(a.score??-1)||a.name.localeCompare(b.name,'zh-CN'));
  return {rows,dates,asof,coverage:dates.length/20,benchmark,universeByDate:Object.fromEntries(values),
    missingDayCount:20-dates.length,algorithm:VERSION,fresh};
}

export function nativeCandidates(summary,day) {
  const events=[],bySymbol=new Map(),rejected=[],seen=new Set();
  const date=summary?.published_market_date||summary?.market_date||null;
  if(summary?.signal_family!==FAMILY)return {events,bySymbol,rejected,date,complete:false,error:'NATIVE_SUMMARY_UNAVAILABLE'};
  for(const row of day.rows) {
    if(row.market_date!==date)continue;
    for(const event of JSON.parse(row.events_json||'[]')) {
      const symbol=fullSymbol(row.symbol);
      if(!symbol || event.symbol!==symbol || event.rule_version!==FAMILY ||
        event.source_id!=='TICKFLOW_NATIVE_15M' || !event.event_id || event.confirmed_at?.slice(0,10)!==date) {
        rejected.push({symbol:row.symbol,event_id:event.event_id,reason:'UNVERIFIED_NATIVE_EVENT'});continue;
      }
      if(seen.has(event.event_id))continue;
      seen.add(event.event_id);
      events.push({...event,name:row.name||'',market_date:date});
    }
  }
  events.sort((a,b)=>b.confirmed_at.localeCompare(a.confirmed_at)||a.event_id.localeCompare(b.event_id));
  for(const event of events) {
    if(!bySymbol.has(event.symbol))bySymbol.set(event.symbol,[]);
    bySymbol.get(event.symbol).push(event);
  }
  return {events,bySymbol,rejected,date,complete:day.total===day.rows.length,error:null};
}

export function project(input) {
  const membership=membershipIndex(input.membership,input.config);
  const candidates=nativeCandidates(input.summary,input.day),intersection=new Map(),unmatched=[];
  const today=input.now.slice(0,10),closed=input.now.slice(11,19)>='15:00:00';
  const reference=[input.market?.as_of?.slice(0,10),candidates.date]
    .filter(d=>d&&(d<today||d===today&&closed)).sort().at(-1);
  const scores=scoreIndustries(input.rotation,membership,input.market,input.now,reference);
  for(const [symbol,events] of candidates.bySymbol) {
    const industries=membership.bySymbol.get(symbol)||[];
    const item={symbol,name:events[0].name,events,industries};
    if(industries.length)intersection.set(symbol,item);else unmatched.push(item);
  }
  for(const row of scores.rows)row.candidates=[...row.members.keys()].filter(s=>intersection.has(s)).map(s=>intersection.get(s));
  return {membership,scores,candidates,intersection,unmatched,observedAt:input.now};
}

export function workbenchLink(event) {
  const params=new URLSearchParams({symbol:event.symbol,period:'15m',event:event.confirmed_at.replace(' ','T'),
    event_id:event.event_id,rule_version:event.rule_version,source:event.source_id});
  for(const key of ['basis_id','factor_revision','basis_revision','risk_text'])if(event[key]!=null)
    params.set(key==='risk_text'?'risk':key,String(event[key]));
  if(event.setup_at)params.set('setup',event.setup_at.replace(' ','T'));
  if(event.adjust_factor>0)params.set('factor',String(event.adjust_factor));
  if(event.defense>0)params.set('defense',String(event.defense));
  return '/lan-kline?'+params;
}
