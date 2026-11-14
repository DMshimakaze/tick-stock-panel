import {INDUSTRY,FIELD} from './model.mjs';

export function beijingNow() {
  return new Date().toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'});
}

/** Only the SDK's authenticated, same-origin public reads are used. Each
 * independent source can fail while the other source remains inspectable. */
export async function loadInputs(api,now=beijingNow()) {
  const errors=[],sources={};
  const read=async(key,label,fn)=>{
    let raw;
    try {
      raw=await fn();
      if(['membership','summary','day'].includes(key)&&(!Array.isArray(raw?.rows)||!Number.isInteger(raw.total)||raw.total<0))
        throw Error('接口缺少有效rows/total');
      if(key==='configs'&&!Array.isArray(raw?.items) || key==='market'&&!Array.isArray(raw?.rows) ||
        key==='pages'&&!Array.isArray(raw) || key==='rotation'&&(!Array.isArray(raw?.dates)||!raw?.columns))
        throw Error('接口返回结构不完整');
      const data=key==='summary'?JSON.parse(raw.rows[0]?.payload_json||'null'):raw;
      if(key==='summary'&&!data)throw Error('原生状态投影为空');
      const count=Array.isArray(raw)?raw.length:(raw.rows||raw.items||raw.dates).length;
      const rowDates=key==='day'?[...new Set(raw.rows.map(r=>r.market_date))]:[];
      sources[key]={ok:true,error:null,read_at:beijingNow(),as_of:key==='summary'?
        data.published_market_date||data.market_date||null:key==='day'?(rowDates.length===1?rowDates[0]:null):
        raw.as_of||raw.dates?.[0]||raw.date||null,total:raw.total??count,received:count,http_status:null};
      if(key==='day')sources[key].storage_date=raw.date??null;
      return data;
    } catch(error) {
      const message=`${label}：${error.message||error}`;errors.push(message);
      sources[key]={ok:false,error:message,read_at:beijingNow(),as_of:null,total:null,received:null,http_status:error.status??null};
      return null;
    }
  };
  const [configs,membership,market,summary,pages]=await Promise.all([
    read('configs','行业配置',()=>api.extDataList()),
    read('membership','行业快照',()=>api.extDataRows(INDUSTRY,{limit:20000,columns:['symbol','name',FIELD]})),
    read('market','全市场比较基准',()=>api.marketSnapshot()),
    read('summary','原生候选状态',()=>api.extDataRows('lan_ma55_native15_summary_v1',{limit:1})),
    read('pages','K线页面状态',()=>api.extPagesList()),
  ]);
  // Request one spare date so an unfinished current session can be discarded.
  // The market snapshot above is also the actual equal-weight comparison input.
  const [rotation,day]=await Promise.all([
    read('rotation','行业20日矩阵',()=>api.rpsRotation(21,'industry')),
    summary?read('day','原生候选',()=>api.extDataRows('lan_ma55_native15_day_v1',{limit:20000})):null,
  ]);
  if(!sources.day)sources.day={ok:false,error:'原生状态不可用，未请求day',read_at:null,as_of:null,total:null,received:null,http_status:null};
  if(rotation&&!rotation.dates.length)errors.push('行业RPS返回空矩阵；尚未取得20个真实交易日。');
  return {config:configs?.items.find(c=>c.id===INDUSTRY)||{id:INDUSTRY},membership,market,summary,day,
    rotation,now,errors,sources,workbench:pages?pages.some(p=>p.enabled&&!p.error&&(p.path==='/lan-kline'||p.managed_by==='lan.kline')):null};
}

/** dimension-members can match separator fragments. The saved exact snapshot
 * remains authoritative; a substring hit cannot create a new membership. */
export async function verifyMembers(api,name,members) {
  const result=await api.dimensionMembers(INDUSTRY,{field:FIELD,value:name,limit:10000});
  const exact=result.rows.filter(r=>r[FIELD]===name && members.has(r.symbol));
  return {total:result.total,received:result.rows.length,complete:result.total===result.rows.length,
    exact:exact.length,excluded:result.rows.length-exact.length};
}
