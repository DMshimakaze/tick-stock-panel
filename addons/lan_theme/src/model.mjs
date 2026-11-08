/** Public projections only. Nothing here calculates or mutates a BUY. */
export const R1='MA55_15M_MINIMAL_V5_R1', V5='MA55_15M_MINIMAL_V5';
export const TREE='lan_theme_tree', STATUS='lan_theme_status', MAX_PARTS=8192;
const canonical=s=>/^\d{6}\.(SH|SZ|BJ)$/.test(s);

export async function readRows(api,id,limit,columns) {
  const data=await api.extDataRows(id,{limit,...(columns?{columns}: {})});
  if(data.total>limit) throw Error(`${id} 超过公开读取上限，停止使用截断结果`);
  return data.rows;
}

export function assembleThemes(status,rows) {
  if(!status?.snapshot_id) return [];
  const current=rows.filter(r=>r.bank===status.bank && r.snapshot_id===status.snapshot_id);
  if(current.length!==status.parts_total) throw Error('题材快照发布尚未完整，保留上次有效视图');
  const groups=new Map();
  for(const row of current) {
    const group=groups.get(row.theme_id)||{parts:row.parts,rows:new Map()};
    if(group.parts!==row.parts || group.rows.has(row.part) || row.part<0 || row.part>=row.parts) throw Error('题材快照分片不一致');
    group.rows.set(row.part,JSON.parse(row.tree_json));groups.set(row.theme_id,group);
  }
  return [...groups.entries()].map(([id,group])=>{
    if(group.rows.size!==group.parts || !group.rows.get(0)?.theme) throw Error('题材快照缺少分片');
    const theme={...group.rows.get(0).theme,nodes:[],memberships:[]};
    if(theme.theme_id!==id) throw Error('题材身份不一致');
    for(let n=0;n<group.parts;n++) {
      theme.nodes.push(...group.rows.get(n).nodes);theme.memberships.push(...group.rows.get(n).memberships);
    }
    return theme;
  }).sort((a,b)=>a.theme_name.localeCompare(b.theme_name,'zh-CN')||a.theme_id.localeCompare(b.theme_id));
}

export function resolveThemes(themes,knownSymbols) {
  const byCode=new Map();
  for(const symbol of knownSymbols) if(canonical(symbol)) {
    if(!byCode.has(symbol.slice(0,6))) byCode.set(symbol.slice(0,6),new Set());
    byCode.get(symbol.slice(0,6)).add(symbol);
  }
  return themes.map(t=>({...t,memberships:t.memberships.map(m=>{
    let symbol=m.symbol;
    if(!canonical(symbol||'')) {
      const raw=m.StockID;
      if(canonical(raw)) symbol=raw;
      else if(/^(SH|SZ|BJ)\d{6}$/.test(raw)) symbol=raw.slice(2)+'.'+raw.slice(0,2);
      else { const found=byCode.get(raw);symbol=found?.size===1?[...found][0]:null; }
    }
    return {...m,symbol:symbol||null,symbol_status:symbol?'RESOLVED':'UNRESOLVED_SYMBOL'};
  })}));
}

export function confirmed(rows,date,{rule=R1,mode='ALL'}={}) {
  const result=new Map();
  for(const row of rows) for(const event of JSON.parse(row.events_json||'[]')) {
    if(event.confirmed_at?.slice(0,10)!==date || event.rule_version!==rule || (mode!=='ALL' && event.mode!==mode)) continue;
    if(!event.event_id || !canonical(event.symbol||row.symbol)) throw Error('已发布确认缺少事件或股票身份');
    if(!result.has(event.event_id)) result.set(event.event_id,{symbol:event.symbol||row.symbol,name:row.name,event,row});
  }
  return [...result.values()].sort((a,b)=>b.event.confirmed_at.localeCompare(a.event.confirmed_at)||a.symbol.localeCompare(b.symbol));
}

export function watching(rows,date,summary) {
  return [...new Map(rows.filter(r=>r.market_date===date && r.phase==='WATCHING')
    .map(row=>[row.symbol,{symbol:row.symbol,name:row.name,event:null,row,
      watch_rule:date===summary.market_date?summary.rule_version:null}])).values()];
}

export function indexThemes(themes) {
  const index=new Map();
  for(const theme of themes) for(const member of theme.memberships) if(member.symbol) {
    const links=index.get(member.symbol)||[];
    links.push({theme,member});index.set(member.symbol,links);
  }
  return index;
}

export function branchContains(theme,key,branch) {
  if(!branch) return true;
  if(branch==='@unconfirmed') return key===null;
  const nodes=new Map(theme.nodes.map(n=>[n.key,n]));
  for(let at=key;at;at=nodes.get(at)?.parent_id) if(at===branch) return true;
  return false;
}

export function selectItems(items,index,{themeId='',branch='',scope='matched',query=''}={}) {
  return items.filter(item=>{
    if(query && !(item.symbol+' '+item.name).toLowerCase().includes(query.toLowerCase())) return false;
    const all=index.get(item.symbol)||[];
    if(scope==='unlabelled') return all.length===0;
    const selected=all.filter(({theme,member})=>(!themeId||theme.theme_id===themeId) && branchContains(theme,member.branch_path_key,branch));
    return scope==='all' && !themeId && !branch || selected.length>0;
  });
}

export function themeCounts(theme,items,branch='') {
  const members=theme.memberships.filter(m=>branchContains(theme,m.branch_path_key,branch));
  const resolved=new Set(members.map(m=>m.symbol).filter(Boolean));
  const sourceIdentities=new Set(members.map(m=>m.symbol||m.stock_identity));
  return {members:sourceIdentities.size,resolved:resolved.size,
    hits:new Set(items.filter(i=>resolved.has(i.symbol)).map(i=>i.symbol)).size};
}

export function statistics(summary,buys,watches,index) {
  const unique=new Set(buys.map(i=>i.symbol));
  return {savedEvents:summary.signals||0,dayEvents:buys.length,uniqueStocks:unique.size,
    replayEvents:buys.filter(i=>i.event.mode==='REPLAY').length,
    liveEvents:buys.filter(i=>i.event.mode==='LIVE').length,
    delayedEvents:buys.filter(i=>i.event.mode==='DELAYED').length,
    watchStocks:new Set(watches.map(i=>i.symbol)).size,
    labelledStocks:[...unique].filter(s=>index.has(s)).length};
}

export function workbenchLink(symbol,event) {
  const params=new URLSearchParams({symbol,period:'15m'});
  for(const [source,key] of [['confirmed_at','event'],['setup_at','setup']]) if(event?.[source]) params.set(key,event[source].replace(' ','T'));
  if(event?.defense>0) params.set('defense',String(event.defense));
  if(event?.adjust_factor>0) params.set('factor',String(event.adjust_factor));
  return '/lan-kline?'+params;
}

export async function readDay(api,date,summary,historyCache) {
  if(date===summary.market_date) {
    const rows=await readRows(api,'lan_ma55_day',20000);
    return {rows:rows.filter(r=>r.market_date===date && r.generation===summary.generation),cycle:summary.cycle};
  }
  if(!historyCache.value) {
    const data=await readRows(api,'lan_ma55_history',1);
    historyCache.value=data.length?JSON.parse(data[0].payload_json):{days:{}};
  }
  const day=historyCache.value.days[date];
  if(!day) throw Error('所选交易日尚无已发布历史；保留当前结果');
  return day;
}
