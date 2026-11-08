import { aggregateMinutes, aggregateFive, dailyCandles, beijing, epoch, PERIODS } from './aggregate.mjs';
import { DEFAULT_MAS, movingAverages, eventAnchor } from './indicators.mjs';
import { createReader, fullSymbol, searchInstruments } from './data.mjs';
import { createWorkbenchChart } from './chart.mjs';
import { createView, esc } from './view.mjs';
import { MA_COLORS } from './theme.mjs';

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
  return {symbol:symbol?fullSymbol(symbol):null,event:timestamp(event),setup:timestamp(q.get('setup')),
    defense:q.has('defense') && Number(q.get('defense'))>0?Number(q.get('defense')):null,period,errors};
}
function preferences(win) {
  try {
    const p=JSON.parse(win.localStorage.getItem(PREFS));
    if (p && Array.isArray(p.mas) && p.mas.length<=12 && new Set(p.mas.map(m=>m.period)).size===p.mas.length
      && p.mas.every(m=>Number.isInteger(m.period) && m.period>=1 && m.period<=2000 && typeof m.enabled==='boolean')
      && Number.isInteger(p.visible) && p.visible>=0 && p.visible<=10000) return {...p,period:PERIODS.includes(p.period)?p.period:'15m'};
  } catch { /* Only local chart preferences are optional. */ }
  return {mas:DEFAULT_MAS.map(m=>({...m})),visible:120,period:'15m'};
}
export function mountWorkbench(el,ctx,{chartFactory=createWorkbenchChart,now=()=>beijing(),readerFactory=createReader}={}) {
  const win=el.ownerDocument.defaultView, link=parseLink(win.location.href), prefs=preferences(win);
  const root=createView(el.ownerDocument);el.append(root);
  const $=s=>root.querySelector(s), reader=readerFactory(ctx.api);
  const model={...prefs,period:new URL(win.location.href).searchParams.has('period')?link.period:prefs.period,
    event:link.event,setup:link.setup,defense:link.defense,eventSymbol:link.symbol,selected:null,data:null,matches:[],error:link.errors.join('；'),loading:false,pinned:false};
  let disposed=false, revision=0, searchRevision=0, current=null, polling=false, plot, needsReset=true;
  const format=(v,digits=model.data?.asset_type==='etf'?3:2)=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits}):'—';
  function readout(bar) {
    if (disposed) return;
    if (!bar.complete) { $('.kw-readout').textContent=bar.timestamp+' · 输入缺口';return; }
    const change=(bar.close/bar.open-1)*100;
    $('.kw-readout').innerHTML=[`<b>${esc(bar.timestamp.slice(0,16))}</b>`,...[['开',bar.open],['高',bar.high],['低',bar.low],['收',bar.close]].map(([k,v])=>`<span>${k} <b>${format(v)}</b></span>`),
      `<span class="${change>=0?'kw-up':'kw-down'}">${change>=0?'+':''}${format(change)}%</span>`,`<span>量 ${format(bar.volume,0)}手</span>`,`<span>额 ${format(Number.isFinite(bar.amount)?bar.amount/1e4:null)}万</span>`,
      ...bar.averages.map(([n,v])=>`<span style="color:${MA_COLORS[model.mas.findIndex(m=>m.period===n)%MA_COLORS.length]}">MA${n} ${format(v,3)}</span>`)].join('');
  }
  try { plot=chartFactory($('.kw-chart'),readout); } catch(e) { model.error='图表初始化失败：'+e.message; }
  function save() { try { win.localStorage.setItem(PREFS,JSON.stringify({mas:model.mas,visible:model.visible,period:model.period})); } catch { model.error='浏览器未允许保存图表偏好'; } }
  function writeUrl() {
    const q=new URLSearchParams({symbol:model.selected.symbol,period:model.period});
    if (model.event) q.set('event',model.event.replace(' ','T'));
    if (model.setup) q.set('setup',model.setup.replace(' ','T'));
    if (model.defense) q.set('defense',String(model.defense));
    win.history.replaceState(null,'','/lan-kline?'+q);
  }
  function controls() {
    root.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===model.period)));
    $('[aria-label="初始显示根数"]').value=[0,40,80,120,240].includes(model.visible)?String(model.visible):'custom';
    $('[aria-label="自定义可见根数"]').value=model.visible||'';
    $('[aria-label="固定关注"]').checked=model.pinned;
    $('.kw-ma-list').innerHTML=model.mas.map((m,i)=>`<label class="kw-chip" style="color:${MA_COLORS[i%MA_COLORS.length]}"><input type="checkbox" aria-label="显示MA${m.period}" data-ma-toggle="${i}" ${m.enabled?'checked':''}>MA${m.period}</label>`).join('');
    $('.kw-ma-edit').innerHTML=model.mas.map((m,i)=>`<div class="kw-ma-row"><label>MA <input type="number" min="1" max="2000" data-ma-period="${i}" aria-label="MA${m.period}周期" value="${m.period}"></label><button data-ma-remove="${i}" aria-label="删除MA${m.period}">×</button></div>`).join('');
    $('[data-action="official"]').disabled=!model.selected || !ctx.ui?.openStockPreview;
    $('[data-action="refresh"]').disabled=!model.selected || model.loading;
    $('[role="alert"]').textContent=model.error;
  }
  function update(reset=false) {
    if (disposed) return;
    controls();
    const data=model.data, calendar=data?.calendar||[];
    current=data?(model.period==='1d'?dailyCandles(data.daily,now(),calendar):model.period==='1m'?aggregateMinutes(data.oneSessions,'1m',now(),calendar):aggregateFive(data.sessions,model.period,now(),calendar))
      :{bars:[],validCount:0,continuous:0,gaps:0,pending:0,dates:[]};
    const enabled=model.mas.filter(m=>m.enabled).map(m=>m.period), averages=movingAverages(current.bars,enabled), anchor=eventAnchor(current.bars,model.event,model.period);
    plot?.update({bars:current.bars,averages,anchor,asset:data?.asset_type,visible:model.visible,
      maColors:new Map(model.mas.map((m,i)=>[m.period,MA_COLORS[i%MA_COLORS.length]])),
      defense:model.event && data?.basis==='QFQ'?model.defense:null},reset || (needsReset && current.validCount>0));
    if (current.validCount) needsReset=false;
    if (!current.validCount) $('.kw-readout').textContent=model.selected?'等待后台预热真实历史…':'选择标的后加载持久行情';
    $('.kw-name').textContent=data?.name||model.selected?.name||'选择股票 / ETF';
    $('.kw-code').textContent=model.selected?.symbol||'—';
    $('.kw-source').textContent=`QuantDash Pro · ${data?.basis==='QFQ'?'前复权':data?'原始价格 · 因子待补':'5m滚动75交易日'} · ${data?.asset_type==='etf'?'ETF':'股票'}`;
    const latest=data?.sessions.flatMap(s=>s.rows).at(-1), previous=data?.daily.findLast(r=>r.date<(latest?.date||''));
    const delta=latest && previous?(latest.close/previous.close-1)*100:null;
    $('.kw-price').innerHTML=latest?`<strong class="${delta>=0?'kw-up':'kw-down'}">${format(latest.close)}</strong><span class="${delta>=0?'kw-up':'kw-down'}">${delta===null?'—':(delta>=0?'+':'')+format(delta)+'%'}</span><div class="kw-muted">最新完成 5m · ${esc(latest.datetime.slice(5,16))}</div>`:'';
    $('.kw-coverage').textContent=`${current.validCount}根 ${model.period==='1d'?'日K':model.period} · ${current.dates.length}交易日 · 末段连续${current.continuous}根${current.gaps?' · 缺口'+current.gaps:''}`;
    $('.kw-asof').textContent='截至 '+(current.dataAsOf?.slice(0,16)||'等待真实数据');
    const phase=data?.status?.phase;
    $('.kw-status').textContent=!model.selected?'按需预热当前标的；普通订阅保留7日，固定关注持续维护。最多50只。':!data?`正在等待 ${model.selected.symbol} 后台预热；分钟额度2次/滚动60秒，首次交接等待不超过60秒。`:
      `${phase==='READY'?'● 已持久化':phase==='PARTIAL'?'◐ 历史有缺口，后台定向补齐':'◌ 预热 / 等待源响应'} · 原始5m ${data.status.periods?.['5m']?.days||0} / 75交易日，${data.status.periods?.['5m']?.bars||0}根 · 1m ${data.status.periods?.['1m']?.days||0} / 20交易日（按需） · ${data.basis_note}${data.status.error?' · '+data.status.error:''}${['QUOTA_WAIT','RATE_LIMITED','BUDGET_WAIT'].includes(data.status.quota)?' · '+({QUOTA_WAIT:'等待分钟额度',RATE_LIMITED:'供应商429冷却中',BUDGET_WAIT:'等待MA55配额协调'}[data.status.quota]):''}`;
    $('.kw-availability').textContent=enabled.filter(n=>current.continuous<n).map(n=>`MA${n}需要${n}根，末段有效${current.continuous}根`).join('；');
    $('.kw-anchor').innerHTML=model.event?`<b>MA55 BUY</b> 原始确认 ${esc(model.event)}${model.setup?' · 支撑测试 '+esc(model.setup):''}${model.defense?' · 防守 '+format(model.defense,3)+'（MA55前复权）':''}<br>${esc(anchor.message)}${data?.basis==='RAW'?' · 当前为原始价，防守价不叠加到图中':''}<button data-action="focus" ${anchor.index<0?'disabled':''}>定位信号</button>`:'';
  }
  function accept(data) {
    if (!data || data.symbol!==model.selected?.symbol) return;
    if (!data.pending) { model.data=data;model.pinned=Boolean(data.status.pinned); }
    update();
  }
  async function load(item, {pinChange=null}={}) {
    const id=++revision, same=model.selected?.symbol===item.symbol;
    if (!same) { model.data=null;model.pinned=false;needsReset=true; }
    if (model.event && item.symbol!==model.eventSymbol) { model.event=null;model.setup=null;model.defense=null; }
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
    const remove=e.target.closest('[data-ma-remove]');
    if (remove) { model.mas.splice(Number(remove.dataset.maRemove),1);save();update();return; }
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
    } else if (action==='add-ma') {
      const n=Number($('[aria-label="新增MA周期"]').value);
      if (!Number.isInteger(n) || n<1 || n>2000 || model.mas.some(m=>m.period===n) || model.mas.length>=12) model.error='周期需为1–2000的不同整数，最多12条MA';
      else { model.mas.push({period:n,enabled:true});model.error='';save(); }update();
    }
  };
  const change=e=>{
    const t=e.target;
    if (t.matches('[aria-label="固定关注"]')) { model.pinned=t.checked;if (model.selected) void load(model.selected,{pinChange:t.checked});return; }
    if (t.matches('[aria-label="初始显示根数"]')) { if (t.value==='custom') $('[aria-label="自定义可见根数"]').focus();else applyVisible(t.value);return; }
    if (t.dataset.maToggle!==undefined) model.mas[Number(t.dataset.maToggle)].enabled=t.checked;
    else if (t.dataset.maPeriod!==undefined) {
      const i=Number(t.dataset.maPeriod),n=Number(t.value);
      if (!Number.isInteger(n) || n<1 || n>2000 || model.mas.some((m,j)=>j!==i && m.period===n)) { model.error='MA周期需为1–2000的不同整数';controls();return; }
      model.mas[i].period=n;
    } else return;
    model.error='';save();update();
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
  if (link.symbol) { $('[aria-label="股票或ETF搜索"]').value=link.symbol;void search(link.symbol,true); }
  return ()=>{disposed=true;revision++;searchRevision++;win.clearInterval(timer);reader.dispose();plot?.dispose();root.remove();};
}
