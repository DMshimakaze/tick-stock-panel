import { beijing, epoch, PERIODS } from './aggregate.mjs';
import { eventAnchor } from './indicators.mjs';
import { createReader, fullSymbol, searchInstruments, savedSeries, savedAverages } from './data.mjs';
import { createWorkbenchChart } from './chart.mjs';
import { createView, esc } from './view.mjs';
import { visibleBuys } from './buy-columns.mjs';

const PREFS='lan.kline.preferences.v1';
export function parseLink(url) {
  const q=new URL(url).searchParams, errors=[];
  let symbol=q.get('symbol'), event=q.get('event'), period=q.get('period')||'15m';
  if (symbol && !fullSymbol(symbol)) { errors.push('URL需要带.SH/.SZ/.BJ的完整代码');symbol=null; }
  if (!PERIODS.includes(period)) { errors.push('URL级别不支持，已选择15m');period='15m'; }
  const timestamp = value => {
    const t=value?.replace('T',' ');
    return t && /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(t) && beijing(epoch(t)*1000)===t ? t : null;
  };
  if (event && !timestamp(event)) errors.push('信号时间无效，未添加标记');
  return {eventRule:q.get('rule_version'),eventId:q.get('event_id'),eventSource:q.get('source'),eventBasis:q.get('basis_id'),eventFactorRevision:q.get('factor_revision'),eventBasisRevision:q.get('basis_revision'),risk:q.get('risk'),symbol:symbol?fullSymbol(symbol):null,event:timestamp(event),setup:timestamp(q.get('setup')),
    factor:q.has('factor')?Number(q.get('factor')):null,defense:q.has('defense') && Number(q.get('defense'))>0?Number(q.get('defense')):null,period,errors};
}
function preferences(win) {
  try {
    const p=JSON.parse(win.localStorage.getItem(PREFS));
    if (p && Number.isInteger(p.visible) && p.visible>=0 && p.visible<=10000)
      return {visible:p.visible,period:PERIODS.includes(p.period)?p.period:'15m'};
  } catch { /* Only local chart preferences are optional. */ }
  return {visible:120,period:'15m'};
}
export function mountWorkbench(el,ctx,{chartFactory=createWorkbenchChart,now=()=>beijing(),readerFactory=createReader}={}) {
  const win=el.ownerDocument.defaultView, link=parseLink(win.location.href), prefs=preferences(win);
  const root=createView(el.ownerDocument);el.append(root);
  const $=s=>root.querySelector(s), reader=readerFactory(ctx.api);
  const model={...prefs,period:new URL(win.location.href).searchParams.has('period')?link.period:prefs.period,
    eventRule:link.eventRule,event:link.event,setup:link.setup,defense:link.defense,factor:link.factor,eventId:link.eventId,eventSource:link.eventSource,eventBasis:link.eventBasis,eventFactorRevision:link.eventFactorRevision,eventBasisRevision:link.eventBasisRevision,risk:link.risk,eventSymbol:link.symbol,selected:null,data:null,sourceError:null,displayPeriod:link.period,matches:[],error:link.errors.join('；'),loading:false,pinned:false};
  let disposed=false, revision=0, searchRevision=0, current=null, polling=false, plot, needsReset=true;
  const format=(v,digits=model.data?.asset_type==='etf'?3:2)=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits}):'—';
  function readout(bar) {
    if (disposed) return;
    if (!bar.complete) { $('.kw-readout').textContent=bar.timestamp+' · 输入缺口';return; }
    const change=(bar.close/bar.open-1)*100;
    $('.kw-readout').innerHTML=[`<b>${esc(bar.timestamp.slice(0,16))}</b>`,...[['开',bar.open],['高',bar.high],['低',bar.low],['收',bar.close]].map(([k,v])=>`<span>${k} <b>${format(v)}</b></span>`),
      `<span class="${change>=0?'kw-up':'kw-down'}">${change>=0?'+':''}${format(change)}%</span>`,`<span>量 ${format(bar.volume,0)}股</span>`,`<span>额 ${format(Number.isFinite(bar.amount)?bar.amount/1e4:null)}万</span>`,
      ...bar.averages.map(([n,v])=>`<span>MA${n} ${format(v,3)}</span>`)].join('');
    $('.kw-event-detail').textContent=(bar.events||[]).map(e=>`BUY ${e.confirmed_at} · 首次记录 ${e.first_seen_at||'—'} · 支撑 ${e.setup_at} · 防守 ${format(e.defense,3)} · ${e.rule_version} · ${e.event_id}${e.risk?' · 风险 '+JSON.stringify(e.risk):''}`).join('；');
  }
  try { plot=chartFactory($('.kw-chart'),readout); } catch(e) { model.error='图表初始化失败：'+e.message; }
  function save() { try { win.localStorage.setItem(PREFS,JSON.stringify({visible:model.visible,period:model.period})); } catch { model.error='浏览器未允许保存图表偏好'; } }
  function writeUrl() {
    const q=new URLSearchParams({symbol:model.selected.symbol,period:model.period});
    if (model.event) q.set('event',model.event.replace(' ','T'));
    if (model.setup) q.set('setup',model.setup.replace(' ','T'));
    if (model.defense) q.set('defense',String(model.defense));
    if (model.factor) q.set('factor',String(model.factor));
    for (const [key,value] of [['rule_version',model.eventRule],['event_id',model.eventId],['source',model.eventSource],['basis_id',model.eventBasis],['factor_revision',model.eventFactorRevision],['basis_revision',model.eventBasisRevision],['risk',model.risk]]) if (value!==null && value!==undefined) q.set(key,String(value));
    win.history.replaceState(null,'','/lan-kline?'+q);
  }
  function controls() {
    root.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===model.period)));
    $('[aria-label="初始显示根数"]').value=[0,40,80,120,240].includes(model.visible)?String(model.visible):'custom';
    $('[aria-label="自定义可见根数"]').value=model.visible||'';
    $('[aria-label="固定关注"]').checked=model.pinned;
    $('[data-action="official"]').disabled=!model.selected || !ctx.ui?.openStockPreview;
    $('[data-action="refresh"]').disabled=!model.selected || model.loading;
    $('[role="alert"]').textContent=model.error;
  }
  function update(reset=false) {
    if (disposed) return;
    controls();
    const data=model.data, calendar=data?.calendar||[];
    const requested=savedSeries(data,model.period);
    if (requested.validCount) model.displayPeriod=model.period;
    current=savedSeries(data,model.displayPeriod);
    const fallback=current.validCount>0 && model.displayPeriod!==model.period;
    const enabled=model.displayPeriod==='15m'?[55,233]:[55], averages=savedAverages(current.bars,enabled), anchor=eventAnchor(current.bars,model.event,model.displayPeriod);
    const buys=visibleBuys(current.bars,data?.events?.rows||[],model.displayPeriod);
    const eventBar=current.bars.find(b=>b.timestamp===model.event);
    const sameSource=!model.eventSource || model.eventSource===data?.source_id || data?.transition==='OVERLAP_VERIFIED';
    const sameRevision=model.eventBasisRevision===null || model.eventBasisRevision===undefined || Number(model.eventBasisRevision)===data?.basis_revision;
    const matchingBasis=data?.basis==='QFQ' && model.factor>0 && eventBar?.adjust_factor===model.factor && sameSource && sameRevision && !data?.correction;
    plot?.update({bars:current.bars,averages,anchor,setupAnchor:eventAnchor(current.bars,model.setup,model.displayPeriod),asset:data?.asset_type,visible:model.visible,
      buys,period:model.displayPeriod,
      defense:model.event && matchingBasis?model.defense:null},reset || (needsReset && requested.validCount>0));
    if (requested.validCount) needsReset=false;
    if (!current.validCount) $('.kw-readout').textContent=model.sourceError?'公共行情服务不可用，请查看服务状态后重试':model.error?'行情读取失败，请重试':model.selected?'等待后台预热真实历史…':'选择标的后加载持久行情';
    $('.kw-name').textContent=data?.name||model.selected?.name||'选择股票 / ETF';
    $('.kw-code').textContent=model.selected?.symbol||'—';
    $('.kw-source').textContent=`lan.ma55 · ${data?.basis==='QFQ'?'前复权':data?'原始价格 · 因子待补':'公共滚动行情'} · ${data?.asset_type==='etf'?'ETF':'股票'}`;
    const latest=current.bars.filter(b=>b.complete).at(-1);
    $('.kw-price').innerHTML=latest?`<strong>${format(latest.close)}</strong><div class="kw-muted">最新完成 ${model.displayPeriod} · ${esc(latest.timestamp.slice(5,16))}</div>`:'';
    $('.kw-coverage').textContent=`${current.validCount}根 ${model.displayPeriod} · ${current.dates.length}交易日 · 末段连续${current.continuous}根${current.gaps?' · 缺口'+current.gaps:''}`;
    $('.kw-asof').textContent='截至 '+(current.dataAsOf?.slice(0,16)||'等待真实数据');
    const phase=data?.status?.phase;
    $('.kw-status').textContent=model.sourceError?`公共行情服务不可用 · ${model.sourceError.error||'请查看公共行情状态'}${current.validCount?' · 保留上次有效图表':''}`:!model.selected?'选择股票后读取公共库已保存的15/30/60m。':!data?`正在读取 ${model.selected.symbol} 的公共滚动行情…`:
      `${phase==='READY'?'● 已保存':phase==='GAP'?'◐ 公共行情有缺口':phase==='FACTOR_PENDING'?'◌ 因子覆盖待补':phase==='DATA_SOURCE_UNAVAILABLE'?'公共行情服务不可用':phase==='SOURCE_UNAVAILABLE'?'公共源暂不可用，显示已保存历史':phase==='REBUILD_PENDING'?'新源窗口重建中，显示迁移历史':phase==='SOURCE_DISCREPANCY'?'数据源存在差异，暂停BUY':'◌ 公共行情预热中'} · 当前周期 ${data.status.row_count||0} / ${data.status.capacity||250}根 · 15m预热 ${data.status.warm_count||0} / 250 · ${data.basis_note||''}${data.status.error?' · '+data.status.error:''}${data.status.source_error?' · '+data.status.source_error:''}${['RATE_LIMITED','QUOTA_WAIT','SOURCE_UNAVAILABLE'].includes(data.status.source_phase)?' · 公共源 '+data.status.source_phase+' 等待'+(data.status.wait_seconds||0)+'秒':''}`;
    $('.kw-source-status').hidden=!model.sourceError;
    $('.kw-availability').textContent=[...(fallback?[`${model.period}尚无已完成K，当前保留${model.displayPeriod}图；后台继续预热`]:[]),...(model.displayPeriod!=='15m'?['当前30/60m不足233根，MA233无值；BUY定位请用15m']:[]),...enabled.filter(n=>current.continuous<n).map(n=>`MA${n}需要${n}根，末段有效${current.continuous}根`)].join('；');
    $('.kw-buy-count').textContent=model.displayPeriod==='15m'?`窗口内 ${buys.length} 条历史 BUY${data?.events?.outside_window?' · 另有 '+data.events.outside_window+' 条确认超出250根滚动窗口':''}${data?.events?.truncated?' · 事件分页达到上限':''}`:'30/60m仅展示聚合K线';
    $('.kw-anchor').innerHTML=model.event?`<b>MA55 BUY</b> 原始确认 ${esc(model.event)}${model.setup?' · 支撑测试 '+esc(model.setup):''}${model.defense?' · 防守 '+format(model.defense,3)+'（MA55前复权）':''}<br>${esc(anchor.message)}${model.eventId?' · '+esc(model.eventId):''}${model.eventRule?' · '+esc(model.eventRule):''}${!matchingBasis?' · 价格基准未证实一致，防守价线未显示':''}${model.risk?' · '+esc(model.risk):''}<button data-action="focus" ${anchor.index<0?'disabled':''}>定位信号</button>`:'';
  }
  function accept(data) {
    if (!data || data.symbol!==model.selected?.symbol) return;
    if (data.unavailable) { model.sourceError=data.status;model.error=data.status.error; }
    else if (!data.pending) { model.sourceError=null;model.error='';model.data=data;model.pinned=Boolean(data.status.pinned); }
    update();
  }
  async function load(item, {pinChange=null}={}) {
    const id=++revision, same=model.selected?.symbol===item.symbol;
    if (!same) { model.data=null;model.sourceError=null;model.displayPeriod=model.period;model.pinned=false;needsReset=true; }
    if (model.event && item.symbol!==model.eventSymbol) { model.event=null;model.setup=null;model.defense=null;model.factor=null;model.eventRule=null;model.eventId=null;model.eventSource=null;model.eventBasis=null;model.eventFactorRevision=null;model.eventBasisRevision=null;model.risk=null; }
    model.selected=item;model.loading=true;model.error='';model.matches=[];$('.kw-results').textContent='';writeUrl();update(!same);
    try { const data=await reader.read(item.symbol,{period:model.period,pinned:pinChange});if (!disposed && id===revision) accept(data); }
    catch(e) { if (id===revision) model.error='数据桥请求失败：'+e.message; }
    finally { if (!disposed && id===revision) { model.loading=false;update(); } }
  }
  async function search(query,auto=false) {
    const id=++searchRevision;model.error='';controls();
    try {
      const matches=await searchInstruments(ctx.api,query);
      if (disposed || id!==searchRevision) return;
      model.matches=matches;
      const exact=matches.find(r=>r.symbol===fullSymbol(query));
      if (auto && exact) { await load(exact);return; }
      $('.kw-results').innerHTML=matches.map(r=>`<button data-symbol="${esc(r.symbol)}">${esc(r.name)} ${esc(r.symbol)}</button>`).join('');
      if (!matches.length) model.error='未找到股票或ETF，请输入名称或完整代码';
    } catch(e) { if (id===searchRevision) model.error='搜索失败：'+e.message; }
    if (!disposed) controls();
  }
  function applyVisible(value) {
    const n=Number(value);
    if (value==='' || !Number.isInteger(n) || (n!==0 && n<10) || n>10000) { model.error='显示根数为10–10000，或选择全部';controls();return; }
    model.visible=n;model.error='';save();update();plot?.range(n);
  }
  const submit=e=>{e.preventDefault();void search($('[aria-label="股票或ETF搜索"]').value,true);};
  const click=e=>{
    const symbol=e.target.closest('[data-symbol]')?.dataset.symbol;
    if (symbol) { void load(model.matches.find(r=>r.symbol===symbol));return; }
    const period=e.target.closest('[data-period]')?.dataset.period;
    if (period) { model.period=period;needsReset=true;save();if (model.selected) void load(model.selected);else update();return; }
    const action=e.target.closest('[data-action]')?.dataset.action;
    if (action==='refresh') void load(model.selected);
    else if (action==='focus') plot?.focus();
    else if (action==='official') ctx.ui?.openStockPreview({symbol:model.selected.symbol,name:model.selected.name});
    else if (action==='range') applyVisible($('[aria-label="初始显示根数"]').value==='0' && !$('[aria-label="自定义可见根数"]').value?'0':$('[aria-label="自定义可见根数"]').value);
    else if (action==='dates') {
      const from=$('[aria-label="起始日期"]').value, to=$('[aria-label="结束日期"]').value;
      const bars=current?.bars.filter(b=>b.timestamp.slice(0,10)>=from && b.timestamp.slice(0,10)<=to);
      if (!from || !to || from>to || !bars?.length) { model.error='所选日期不在当前历史范围内';controls();return; }
      model.error='';controls();plot?.dates(bars[0].time,bars.at(-1).time);
    }
  };
  const change=e=>{
    const t=e.target;
    if (t.matches('[aria-label="固定关注"]')) { model.pinned=t.checked;if (model.selected) void load(model.selected,{pinChange:t.checked});return; }
    if (t.matches('[aria-label="初始显示根数"]')) { if (t.value==='custom') $('[aria-label="自定义可见根数"]').focus();else applyVisible(t.value);return; }

  };
  const poll=async()=>{
    if (disposed || polling || model.loading || !model.selected || win.document.hidden) return;
    polling=true;
    try { const id=revision,data=await reader.poll();if (!disposed && id===revision) accept(data); }
    catch(e) { if (!disposed) { model.error='数据桥读取失败：'+e.message;controls(); } }
    finally { polling=false; }
  };
  $('.kw-search').addEventListener('submit',submit);root.addEventListener('click',click);root.addEventListener('change',change);
  const timer=win.setInterval(poll,3000);update();
  if (link.symbol) { $('[aria-label="股票或ETF搜索"]').value=link.symbol;void load({symbol:link.symbol,name:link.symbol}); }
  return ()=>{disposed=true;revision++;searchRevision++;win.clearInterval(timer);reader.dispose();plot?.dispose();root.remove();};
}
