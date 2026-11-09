import {R1,V5} from '../src/model.mjs';
export const date='2026-11-06';
export const event=(id,symbol,mode='REPLAY',rule=R1)=>({event_id:id,symbol,rule_version:rule,mode,confirmed_at:date+' 10:15:00',setup_at:date+' 09:45:00',first_seen_at:'2026-11-08 18:00:00',defense:10.5,adjust_factor:1.25});
export const rows=[
  {symbol:'000001.SZ',name:'DEMO甲',market_date:date,generation:'g',phase:'IDLE',events_json:JSON.stringify([event('a','000001.SZ','LIVE'),event('b','000001.SZ','DELAYED'),event('old','000001.SZ','REPLAY',V5)])},
  {symbol:'300863.SZ',name:'DEMO乙',market_date:date,generation:'g',phase:'IDLE',events_json:JSON.stringify([event('c','300863.SZ')])},
  {symbol:'600519.SH',name:'DEMO丙',market_date:date,generation:'g',phase:'WATCHING',events_json:'[]',bar_end:date+' 15:00:00'},
  {symbol:'600000.SH',name:'DEMO无标签',market_date:date,generation:'g',phase:'IDLE',events_json:JSON.stringify([event('d','600000.SH')])},
];
const member=(stock,branch,path)=>({StockID:stock,stock_identity:stock,symbol:stock.includes('.')?stock:({'000001':'000001.SZ','300863':'300863.SZ','600519':'600519.SH'}[stock]),symbol_status:'RESOLVED',symbol_resolution:{kind:'ADMIN_EXPLICIT_MAP',source:'DEMO explicit map',asof_cn:'2026-11-06 00:00:00'},branch_path_key:branch,branch_path:path});
export const themes=[
  {theme_id:'T1',theme_name:'DEMO制造',nodes:[{key:'l1',parent_id:null,name:'供应商',depth:1,path:['供应商']},{key:'l2',parent_id:'l1',name:'整机',depth:2,path:['供应商','整机']}],memberships:[member('000001','l1',['供应商']),member('300863','l2',['供应商','整机']),member('300863','l1',['供应商']),member('600519',null,[])]},
  {theme_id:'T2',theme_name:'DEMO服务',nodes:[],memberships:[member('000001.SZ',null,[])]},
];
export const status={api_version:1,bank:'A',parts_total:2,snapshot_id:'s1',snapshot_asof:'2026-11-09 01:00:00',snapshot_state:'READY',demo:true,source:'DEMO ONLY',source_kind:'KPL_INFOGET',catalog_coverage:'UNKNOWN',counts:{themes:2,nodes:2,memberships:5}};
export const treeRows=themes.map(t=>({bank:'A',theme_id:t.theme_id,part:0,parts:1,snapshot_id:'s1',tree_json:JSON.stringify({theme:{theme_id:t.theme_id,theme_name:t.theme_name},nodes:t.nodes,memberships:t.memberships})}));
export const summary={market_date:date,available_dates:[date,'2026-11-05'],generation:'g',signals:100,rule_version:R1,history_published_at:'v1',cycle:{processed_symbols:4,expected_symbols:4}};
export function sdk({enabled=true,empty=false}={}) {
  const calls=[],previews=[];
  return {calls,previews,ui:{openStockPreview:p=>previews.push(p)},api:{
    async extPagesList(){calls.push(['pages']);return [{path:'/lan-kline',managed_by:'lan.kline',enabled}];},
    async extDataRows(id,options){calls.push([id,options]);const data={lan_theme_status:[{payload_json:JSON.stringify(empty?{api_version:1,snapshot_id:null,counts:{themes:0,nodes:0,memberships:0}}:status)}],lan_theme_tree:treeRows,lan_ma55_summary:[{payload_json:JSON.stringify(summary)}],lan_ma55_day:rows,lan_ma55_history:[{payload_json:JSON.stringify({days:{'2026-11-05':{rows:[],cycle:{}}}})}]}[id];if(!data)throw Error('Unexpected API '+id);return {rows:data,total:data.length};}
  }};
}
