export const dates = ['2026-11-13','2026-11-12','2026-11-11','2026-11-10','2026-11-09',
  '2026-11-06','2026-11-05','2026-11-04','2026-11-03','2026-11-02',
  '2026-10-30','2026-10-29','2026-10-28','2026-10-27','2026-10-26',
  '2026-10-23','2026-10-22','2026-10-21','2026-10-20','2026-10-19'];
export const now = '2026-11-14 11:00:00';
export const field = '所属同花顺行业';
export const config = { id:'ext_hy_ths', mode:'snapshot', description:'同花顺行业分类，扁平板块无层级',
  latest_sync_date:'2026-11-13 22:38:15', updated_at:'2026-11-13T22:38:15.232996' };
export const symbol = n => String(n).padStart(6,'0')+'.SZ';
export function fixture() {
  const names=Array.from({length:24},(_,i)=>'行业'+String(i).padStart(2,'0'));
  const rows=names.map((label,i)=>({symbol:symbol(i+1),name:'股票'+i,[field]:label}));
  const columns=Object.fromEntries(dates.map((d,i)=>{
    const order=i<5?[names[0],names[23],...names.slice(2,23),names[1]]:names;
    return [d,order.map((name,j)=>[name,0.024-j*.001])];
  }));
  const event={event_id:'native:test:1',symbol:symbol(1),confirmed_at:'2026-11-13 14:15:00',
    setup_at:'2026-11-13 14:00:00',mode:'REPLAY',source_id:'TICKFLOW_NATIVE_15M',
    rule_version:'NATIVE15_REVIEW_V1',basis_id:'native-raw',factor_revision:3,basis_revision:4};
  return {config:{...config},membership:{total:rows.length,rows},
    rotation:{dates:[...dates],columns,concept_count:names.length},
    market:{as_of:dates[0],rows:[{symbol:symbol(1),change_pct:.01},{symbol:symbol(2),change_pct:-.01}]},
    summary:{signal_family:'NATIVE15_REVIEW_V1',market_date:dates[0],published_market_date:dates[0],generation:2},
    day:{total:1,rows:[{symbol:symbol(1),name:'股票0',market_date:dates[0],events_json:JSON.stringify([event])}]},
    event,now};
}
export function sdk(input=fixture()) {
  const calls=[];
  const api={
    extDataList:async()=>{calls.push(['configs']);return {items:[input.config]};},
    marketSnapshot:async()=>{calls.push(['market']);return input.market;},
    rpsRotation:async(...args)=>{calls.push(['rps',...args]);return input.rotation;},
    extDataRows:async(id,opts)=>{
      calls.push(['rows',id,opts]);
      if(id==='ext_hy_ths')return input.membership;
      if(id==='lan_ma55_native15_summary_v1')return {total:1,rows:[{payload_json:JSON.stringify(input.summary)}]};
      if(id==='lan_ma55_native15_day_v1')return input.day;
      throw Error('Unexpected table '+id);
    },
    extPagesList:async()=>[{enabled:true,path:'/lan-kline'}],
    dimensionMembers:async(id,opts)=>{
      calls.push(['members',id,opts]);
      const rows=input.membership.rows.filter(r=>r[field]===opts.value);
      return {id,field:opts.field,value:opts.value,total:rows.length,rows};
    },
  };
  return {api,calls,input,ui:{openStockPreview:()=>true}};
}
