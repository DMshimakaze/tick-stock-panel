/** Entirely synthetic DEMO relationships and events; never production data. */
import {R1,V5} from '../src/model.mjs';
export function largeFixture() {
  const date='2026-11-06',symbol=i=>String(100000+i)+'.SZ';
  const rows=Array.from({length:6000},(_,i)=>{
    const e={event_id:'DEMO-'+i,symbol:symbol(i),rule_version:i%10?R1:V5,mode:['LIVE','DELAYED','REPLAY'][i%3],confirmed_at:date+' 10:15:00',setup_at:date+' 09:45:00',first_seen_at:'2026-11-09 00:00:00',defense:10,adjust_factor:1};
    return {symbol:symbol(i),name:'DEMO股票'+i,market_date:date,generation:7001,phase:i%5===0?'WATCHING':'IDLE',category:'DEMO',reason:'Synthetic fixture',bar_end:date+' 15:00:00',processed_at:date+' 15:00:00',decision_json:'{}',events_json:JSON.stringify([e])};
  });
  const themes=Array.from({length:300},(_,t)=>{
    const theme_id='DEMO-T'+t;
    const nodes=Array.from({length:5},(_,n)=>({key:theme_id+'/'+n,node_id:String(n),parent_id:n?theme_id+'/0':null,name:'DEMO细分'+n,depth:n?2:1,path:n?['DEMO一级','DEMO子'+n]:['DEMO一级']}));
    const memberships=Array.from({length:100},(_,n)=>{
      const node=n%20?nodes[n%5]:null,code=symbol((t*100+n)%6000);
      return {StockID:code,stock_identity:code,symbol:code,symbol_status:'RESOLVED',symbol_resolution:{kind:'SOURCE_EXCHANGE'},branch_path_key:node?.key||null,branch_path:node?.path||[],prod_name:'DEMO',Reason:'Synthetic relation only'};
    });
    return {theme_id,theme_name:'DEMO大题材'+String(t).padStart(3,'0'),nodes,memberships};
  });
  const status={api_version:1,snapshot_id:'DEMO-scale-1',bank:'A',parts_total:300,snapshot_state:'READY',snapshot_asof:'2026-11-09 00:00:00',demo:true,source:'DEMO scale benchmark',provenance:'Synthetic only; no commercial source',catalog_coverage:'UNKNOWN',counts:{themes:300,nodes:1500,memberships:30000}};
  const summary={market_date:date,available_dates:[date],generation:7001,revision:1,signals:6000,rule_version:R1};
  const treeRows=themes.map(t=>({bank:'A',theme_id:t.theme_id,part:0,parts:1,snapshot_id:status.snapshot_id,tree_json:JSON.stringify({theme:{theme_id:t.theme_id,theme_name:t.theme_name},nodes:t.nodes,memberships:t.memberships})}));
  return {date,rows,themes,status,summary,treeRows};
}
