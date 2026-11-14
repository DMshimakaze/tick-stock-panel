import {project,workbenchLink,VERSION} from './model.mjs';
import {loadInputs,beijingNow,verifyMembers} from './data.mjs';
import {CSS,esc} from './styles.mjs';

const modeLabel=mode=>({LIVE:'实时确认',DELAYED:'延迟确认',REPLAY:'历史重放'}[mode]||'来源模式未知');
const statusLabel=status=>({OK:'同期保存快照',STALE_MEMBERSHIP:'分类快照滞后或时间未知',
  STALE_RPS:'日频矩阵落后于已知完成交易日',INSUFFICIENT_HISTORY:'20日历史不足',INCOMPLETE_MEMBERSHIP:'分类返回不完整'}[status]||status);
const pct=value=>value===null?'—':(value*100).toFixed(1)+'%';
const signed=value=>value===null?'—':(value>=0?'+':'')+(value*100).toFixed(1);

export function mountIndustry(el,ctx,{now=beijingNow}={}) {
  const root=el.ownerDocument.createElement('section');root.className='sf';el.append(root);
  let disposed=false,loading=false,epoch=0;
  const state={view:null,input:null,errors:[],stale:false,selected:'',category:'全部',query:'',industryPage:0,
    memberPage:0,memberMode:'candidate',unmatched:false,checks:new Map(),readMs:0,computeMs:0,cacheHits:0};
  const active=()=>state.view?.scores.rows.find(r=>r.name===state.selected);
  function render() {
    if(disposed)return;
    const v=state.view,s=v?.scores,m=v?.membership,c=v?.candidates,selected=active();
    const rows=(s?.rows||[]).filter(r=>(state.category==='全部'||r.category===state.category)&&r.name.toLowerCase().includes(state.query.toLowerCase()));
    const pages=Math.max(1,Math.ceil(rows.length/12));state.industryPage=Math.min(state.industryPage,pages-1);
    const stocks=state.unmatched?v?.unmatched||[]:selected?[...selected.members.values()].map(member=>
      v.intersection.get(member.symbol)||{...member,industries:m.bySymbol.get(member.symbol)||[],events:[]}):[];
    const filtered=stocks.filter(stock=>state.unmatched||state.memberMode==='all'||stock.events.length);
    const memberPages=Math.max(1,Math.ceil(filtered.length/15));state.memberPage=Math.min(state.memberPage,memberPages-1);
    const check=state.checks.get(state.selected);
    root.innerHTML=`<style>${CSS}</style>
      <header class="sf-top"><div><h1>行业强度 × MA55</h1><div class="sf-subtitle">同花顺行业（扁平） · 20个已完成交易日</div></div>
        <button data-action="refresh" ${loading?'disabled':''}>${loading?'正在读取…':'重读本机数据'}</button></header>
      <div class="sf-error" role="alert">${esc(state.errors.join('\n'))}</div>
      ${state.stale?'<p class="sf-warning">本次读取未取得完整结果，保留上次快照；当前不能视为新排名。</p>':''}
      ${m?.pending.length?`<p class="sf-warning">${m.pending.length.toLocaleString()}条分类待核实：来源把多个标签用“-”拼接，无法还原权威行业身份。当前榜单仅覆盖${m.sectors.size}个明确扁平标签。</p>`:''}
      ${s&&!s.rows.some(r=>r.valid)?`<p class="sf-warning">${s.dates.length<20?'INSUFFICIENT_HISTORY · 真实交易日不足20个':statusLabel(s.rows[0]?.status||'行业分类尚不可用')}；有效排名尚未生成。</p>`:''}
      <div class="sf-kpis" aria-label="核心统计">
        ${[['可用行业数',s?s.rows.filter(r=>r.valid).length:'—'],['20日有效覆盖',s?`${s.dates.length} / 20`:'—'],
          ['强势行业数',s?s.rows.filter(r=>r.valid&&['持续强势','新晋走强'].includes(r.category)).length:'—'],
          ['当前 MA55 交集数',selected&&c?.complete?selected.candidates.length:'—']].map(([label,value])=>`<div class="sf-kpi"><span>${label}</span><strong>${value}</strong></div>`).join('')}
      </div>
      <p class="sf-muted">${s?.asof?`评分截至 ${esc(s.asof)} 收盘`:'等待日频矩阵'} · ${c?.date?`候选来源交易日 ${esc(c.date)} · ${c.events.length}条 / ${c.bySymbol.size}只`:'原生候选尚不可用'} · 当前归属回看历史，交集用于盘后观察。</p>
      <details class="sf-details"><summary>来源、分类待核实与评分口径</summary>
        <p>仅行业参与评分。分类来源：Fuyao / 同花顺已有扩展表；${esc(m?.description||'未取得来源说明')}。</p>
        <p>快照文件写入时间：${esc(m?.syncAt||'未知')}；供应商原始生效时间未提供。只表示本机保存版本，不证明历史当时归属。</p>
        <p>版本：${esc(m?.version||'—')} · ${esc(VERSION)}</p>
        <p>当前有效分类子集内，每日按成员平均日涨跌幅重新排序。强度 = 100 × (70% × 20日Top10天数/20 + 30% × 最近5日平均排名百分位)。百分位 = 1 − (排名−1)/(当日可用行业数−1)。仅一个行业时记1。</p>
        <p>5日变化 = 最近5日平均排名百分位 − 20日平均排名百分位。持续强势：Top10≥10天且近5日百分位≥60%；新晋走强：变化≥20个百分点且近5日≥60%；明显退潮：变化≤−20个百分点且近5日≤40%。其余观察。</p>
        <p>相对全市场只比较最新同日的平均日涨跌幅，单位为百分点；基准覆盖${s?.benchmark.coverage||0}只。不是行业指数20日收益。资金净流入：未接入；AI未参与评分。缺值保持NA，不补零。</p>
        <p>行业股票快照 ${m?`${m.received}/${m.total}`:'—'}条 · 已明确归属 ${m?.bySymbol.size||0}只 · 原生候选交集共${v?.intersection.size||0}只 · 未映射候选${v?.unmatched.length||0}只。完整证券代码无效${m?.invalidSymbols.length||0}条。</p>
        <button data-action="unmatched">查看未映射候选</button>
        <p>真实评分日期：${esc(s?.dates.join('、')||'暂无')}。不足时尚缺${s?.missingDayCount??20}个有数据交易日，未凭自然日推造日期。</p>
        <ul>${(m?.pending||[]).slice(0,12).map(r=>`<li>${esc(r.symbol)} · ${esc(r.raw_label||'无标签')}</li>`).join('')}</ul>
      </details>
      <div class="sf-layout">
        <section class="sf-card"><div class="sf-title"><h2>行业榜</h2><small>明确分类子集 · 非全行业指数榜</small></div>
          <div class="sf-tools"><select data-control="category" aria-label="行业类别">${['全部','持续强势','新晋走强','明显退潮','观察','数据待核实'].map(t=>`<option ${state.category===t?'selected':''}>${t}</option>`).join('')}</select>
            <form data-action="search"><input aria-label="行业搜索" name="query" value="${esc(state.query)}" placeholder="精确行业名称中搜索"><button>搜索</button></form></div>
          <div class="sf-rank-head"><span>行业 / 交集</span><span>Top10</span><span>5日变化</span><span>强度</span></div>
          <div aria-label="行业列表">${rows.slice(state.industryPage*12,(state.industryPage+1)*12).map(row=>`<button class="sf-industry" data-sector="${esc(row.name)}" aria-pressed="${!state.unmatched&&state.selected===row.name}">
            <span><strong>${esc(row.name)}</strong><small>${row.members.size}只成分 · ${row.candidates.length}只候选</small><span class="sf-category" data-kind="${row.category}">${row.category}</span></span>
            <span>${row.top10}/${row.observedDays}</span><span>${signed(row.delta)}<small>百分点</small></span><span>${row.score??'—'}</span></button>`).join('')||'<p class="sf-empty">暂无符合条件的行业。</p>'}</div>
          <div class="sf-pager"><button data-action="industry-prev" ${state.industryPage===0?'disabled':''}>上一页</button><span>${state.industryPage+1} / ${pages}</span><button data-action="industry-next" ${state.industryPage+1>=pages?'disabled':''}>下一页</button></div>
        </section>
        <section class="sf-card" aria-label="行业成分与原生候选"><div class="sf-title"><h2>${state.unmatched?'未映射候选':esc(selected?.name||'请选择行业')}</h2><small>${filtered.length}只证券</small></div>
          ${selected&&!state.unmatched?`<p class="sf-muted">${statusLabel(selected.status)} · 历史覆盖${selected.observedDays}/20 · Top20 ${selected.top20}天 · 近5日百分位${pct(selected.recent5)} · 最新日相对全市场 ${signed(selected.relativeMarket)}个百分点</p>
            ${selected.missingDates.length?`<p class="sf-warning">缺失日期：${esc(selected.missingDates.join('、'))}</p>`:''}
            <div class="sf-view-switch"><button data-member-mode="candidate" aria-pressed="${state.memberMode==='candidate'}">MA55交集</button><button data-member-mode="all" aria-pressed="${state.memberMode==='all'}">全部成分</button></div>
            <p class="sf-muted">${check?.error?esc(check.error):check?.result?`公共成分接口返回${check.result.received}/${check.result.total}条，精确匹配${check.result.exact}条；排除${check.result.excluded}条片段匹配。${check.result.complete?'':'接口返回不完整，未用于新增归属。'}`:'成分归属来自已读取的完整股票快照。'}</p>`:''}
          ${c&&!c.complete?'<p class="sf-warning">原生候选投影不可用或返回不完整，不能将当前数量视为完整交集。</p>':''}
          <div class="sf-members">${filtered.slice(state.memberPage*15,(state.memberPage+1)*15).map(stock=>`<article class="sf-stock" data-symbol="${esc(stock.symbol)}">
            <div class="sf-stock-header"><div><strong>${esc(stock.name||stock.symbol)}</strong><small> ${esc(stock.symbol)}</small></div><button data-preview="${esc(stock.symbol)}">个股详情</button></div>
            <small>${stock.industries.length?`所属行业：${esc(stock.industries.join('、'))}`:'无已核实行业归属；ETF等未强行贴标签'}</small>
            <div class="sf-events">${stock.events.map(event=>`${state.input?.workbench?`<a class="sf-event" data-candidate="${esc(event.event_id)}" href="${esc(workbenchLink(event))}" target="_blank" rel="noreferrer">`:'<span class="sf-event">'}
              ${esc(event.confirmed_at)} · ${esc(event.mode||'UNKNOWN')} / ${modeLabel(event.mode)} · 15m
              <small> ${esc(event.source_id)} · ${esc(event.market_date)}</small><code>${esc(event.event_id)}</code>${state.input?.workbench?'</a>':'</span>'}`).join('')||'<small>该证券没有当日已发布原生15m候选。</small>'}</div></article>`).join('')||'<p class="sf-empty">本行业当前没有对应原生15m候选；可切换“全部成分”。</p>'}</div>
          ${state.input&&!state.input.workbench?'<p class="sf-warning">15m工作台未启用，可使用官方个股详情。</p>':''}
          <div class="sf-pager"><button data-action="member-prev" ${state.memberPage===0?'disabled':''}>成分上一页</button><span>${state.memberPage+1} / ${memberPages}</span><button data-action="member-next" ${state.memberPage+1>=memberPages?'disabled':''}>成分下一页</button></div>
        </section>
      </div>
      <footer class="sf-foot">读取 ${state.readMs.toFixed(1)}ms · 计算 ${state.computeMs.toFixed(1)}ms · 筛选/分页复用当前快照 ${state.cacheHits}次 · 观察时间 ${esc(v?.observedAt||'—')}。页面只读，不改变MA55计算、采集频率或候选事件；收盘后评分不回填成盘中确认理由。</footer>`;
  }
  async function checkSelected() {
    const row=active();if(!row||state.checks.has(row.name))return;
    const token=epoch;state.checks.set(row.name,{});
    try {
      const result=await verifyMembers(ctx.api,row.name,row.members);
      if(!disposed&&token===epoch)state.checks.set(row.name,{result});
    } catch(error) { if(!disposed&&token===epoch)state.checks.set(row.name,{error:`成分接口校验未完成：${error.message||error}；保持快照精确归属。`}); }
    if(!disposed&&token===epoch)render();
  }
  async function refresh() {
    if(loading)return;loading=true;epoch++;render();
    try {
      const start=performance.now(),input=await loadInputs(ctx.api,now());
      const loaded=performance.now(),view=project(input);state.computeMs=performance.now()-loaded;state.readMs=loaded-start;
      if(disposed)return;
      state.errors=input.errors;
      if(state.view?.scores.dates.length&&(!view.scores.dates.length||!view.membership.received))state.stale=true;
      else {
        state.view=view;state.input=input;state.stale=false;state.checks=new Map();
        if(!view.scores.rows.some(r=>r.name===state.selected))state.selected=view.scores.rows[0]?.name||'';
      }
    } catch(error) { state.errors=[`读取/计算未完成：${error.message||error}`];state.stale=!!state.view; }
    finally { if(!disposed){loading=false;render();checkSelected();} }
  }
  const click=event=>{
    const button=event.target.closest('button');if(!button||button.disabled)return;
    if(button.dataset.action==='refresh'){refresh();return;}
    if(button.dataset.preview){
      const symbol=button.dataset.preview,stock=state.view?.intersection.get(symbol)||state.view?.unmatched.find(s=>s.symbol===symbol);
      ctx.ui?.openStockPreview({symbol,name:stock?.name});return;
    }
    if(button.dataset.sector){state.selected=button.dataset.sector;state.unmatched=false;state.memberPage=0;}
    if(button.dataset.memberMode){state.memberMode=button.dataset.memberMode;state.memberPage=0;}
    const action=button.dataset.action;
    if(action==='unmatched'){state.unmatched=true;state.memberPage=0;}
    if(action==='industry-prev')state.industryPage--;
    if(action==='industry-next')state.industryPage++;
    if(action==='member-prev')state.memberPage--;
    if(action==='member-next')state.memberPage++;
    if(!button.dataset.sector&&!button.dataset.memberMode&&!action)return;
    state.cacheHits++;render();if(button.dataset.sector)checkSelected();
  };
  const change=event=>{if(event.target.dataset.control==='category'){
    state.category=event.target.value;state.industryPage=0;state.cacheHits++;render();
  }};
  const submit=event=>{event.preventDefault();state.query=event.target.querySelector('input').value.trim();state.industryPage=0;state.cacheHits++;render();};
  root.addEventListener('click',click);root.addEventListener('change',change);root.addEventListener('submit',submit);
  refresh();
  return ()=>{disposed=true;epoch++;root.removeEventListener('click',click);root.removeEventListener('change',change);root.removeEventListener('submit',submit);root.remove();};
}
