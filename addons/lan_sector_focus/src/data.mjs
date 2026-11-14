import {INDUSTRY,FIELD} from './model.mjs';

export function beijingNow() {
  return new Date().toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'});
}

/** Only the SDK's authenticated, same-origin public reads are used. Each
 * independent source can fail while the other source remains inspectable. */
export async function loadInputs(api,now=beijingNow()) {
  const errors=[];
  const read=async(label,fn,empty)=>{
    try { return await fn(); }
    catch(error) { errors.push(`${label}：${error.message||error}`);return empty; }
  };
  const [configs,membership,market,summaryTable,pages]=await Promise.all([
    read('行业配置',()=>api.extDataList(),{items:[]}),
    read('行业快照',()=>api.extDataRows(INDUSTRY,{limit:20000,columns:['symbol','name',FIELD]}),{total:0,rows:[]}),
    read('全市场比较基准',()=>api.marketSnapshot(),{as_of:null,rows:[]}),
    read('原生候选状态',()=>api.extDataRows('lan_ma55_native15_summary_v1',{limit:1}),{total:0,rows:[]}),
    read('K线页面状态',()=>api.extPagesList(),[]),
  ]);
  const summary=summaryTable.rows.length?JSON.parse(summaryTable.rows[0].payload_json):null;
  // Request one spare date so an unfinished current session can be discarded.
  // The market snapshot above is also the actual equal-weight comparison input.
  const [rotation,day]=await Promise.all([
    read('行业20日矩阵',()=>api.rpsRotation(21,'industry'),{dates:[],columns:{}}),
    summary?read('原生候选',()=>api.extDataRows('lan_ma55_native15_day_v1',{limit:20000}),{total:0,rows:[]}):{total:0,rows:[]},
  ]);
  if(!rotation.dates.length)errors.push('行业RPS返回空矩阵；尚未取得20个真实交易日。');
  return {config:configs.items.find(c=>c.id===INDUSTRY)||{id:INDUSTRY},membership,market,summary,day,
    rotation,now,errors,workbench:pages.some(p=>p.enabled&&!p.error&&(p.path==='/lan-kline'||p.managed_by==='lan.kline'))};
}

/** dimension-members can match separator fragments. The saved exact snapshot
 * remains authoritative; a substring hit cannot create a new membership. */
export async function verifyMembers(api,name,members) {
  const result=await api.dimensionMembers(INDUSTRY,{field:FIELD,value:name,limit:10000});
  const exact=result.rows.filter(r=>r[FIELD]===name && members.has(r.symbol));
  return {total:result.total,received:result.rows.length,complete:result.total===result.rows.length,
    exact:exact.length,excluded:result.rows.length-exact.length};
}
