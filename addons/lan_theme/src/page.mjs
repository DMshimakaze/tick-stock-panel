import {R1,V5,STATUS,TREE,MAX_PARTS,readRows,assembleThemes,resolveThemes,confirmed,watching,indexThemes,selectItems,themeCounts,statistics,workbenchLink,readDay} from './model.mjs';
import {CSS,esc} from './view.mjs';

const sourceLabel=m=>({REPLAY:'历史重放',LIVE:'实时确认',DELAYED:'延迟确认'}[m]||'未标明');

export function mountTheme(el,ctx) {
  const root=el.ownerDocument.createElement('section');root.className='lt';el.append(root);
  const model={status:null,themes:[],summary:null,rows:[],date:'',cycle:null,rule:R1,mode:'ALL',tab:'BUY',scope:'matched',themeId:'',branch:'',themeQuery:'',stockQuery:'',reverse:'',page:0,loading:true,error:'',workbench:'checking'};
  let disposed=false,request=0,history={value:null},historyRevision=null,treeSnapshot=null;
  const collapsed=new Set();
  function data() {
    const themes=resolveThemes(model.themes,model.rows.map(r=>r.symbol)),index=indexThemes(themes);
    const buys=confirmed(model.rows,model.date,{rule:model.rule,mode:model.mode});
    const watches=watching(model.rows,model.date,model.summary||{}),items=model.tab==='BUY'?buys:watches;
    const selected=selectItems(items,index,{themeId:model.themeId,branch:model.branch,scope:model.scope,query:model.stockQuery});
    return {themes,index,buys,watches,items,selected,stats:statistics(model.summary||{},buys,watches,index)};
  }
  function render() {
    if(disposed) return;
    let d;
    try { d=data(); } catch(e) { model.error=e.message;d={themes:[],index:new Map(),buys:[],watches:[],items:[],selected:[],stats:statistics({},[],[],new Map())}; }
    const s=model.status,summary=model.summary||{},stats=d.stats,active=d.themes.find(t=>t.theme_id===model.themeId);
    const dates=[...new Set([summary.market_date,...(summary.available_dates||[])].filter(Boolean))].sort().reverse();
    const themeList=d.themes.filter(t=>(t.theme_name+' '+t.theme_id).toLowerCase().includes(model.themeQuery.toLowerCase()));
    const branchButton=(node)=>{const count=themeCounts(active,d.items,node.key);return `<button class="lt-branch" data-branch="${esc(node.key)}" aria-pressed="${model.branch===node.key}">${esc(node.path.join(' / '))}<small> · ${count.hits}命中 / ${count.members}成员</small></button>`;};
    const count=active?themeCounts(active,d.items):null;
    const pageCount=Math.max(1,Math.ceil(d.selected.length/40));model.page=Math.min(model.page,pageCount-1);
    const shown=d.selected.slice(model.page*40,(model.page+1)*40);
    const reverseLinks=d.index.get(model.reverse)||[];
    const historical=s?.snapshot_asof && model.date && s.snapshot_asof.slice(0,10)!==model.date;
    root.innerHTML=`<style>${CSS}</style>
      <header class="lt-top"><div><h1>题材与 MA55</h1><small>兰 · 1.0.0 · 已发布信号的题材归属筛选</small></div><button data-action="refresh" ${model.loading?'disabled':''}>${model.loading?'正在读取…':'刷新已发布数据'}</button></header>
      <div class="lt-error" role="alert">${esc(model.error)}</div>
      <section class="lt-card lt-source" aria-label="题材来源状态">${s?.demo?'<b class="lt-demo">DEMO · 隔离测试关系</b>':''}
        <strong>${!s?'等待本地题材状态':!s.snapshot_id?'待导入授权题材快照':s.snapshot_state==='STALE'?'快照已保留 · 最近导入未通过':'已导入题材快照'}</strong>
        <p>${s?.snapshot_id?`${esc(s.source)} · ${esc(s.source_kind)} · 快照 ${esc(s.snapshot_asof)}`:'请用本插件导入工具导入合法获取的 InfoGet JSON 或标准化导出文件。'}</p>
        <small>${s?.snapshot_id?`题材 ${s.counts.themes} · 节点 ${s.counts.nodes} · 股票关系 ${s.counts.memberships} · 目录覆盖 ${s.catalog_coverage==='MANIFEST_COMPLETE'?'声明目录已齐（非KPL全量证明）':'部分 / 未知'}`:'AUTHORIZED_SNAPSHOT_REQUIRED'} · 在线KPL未接入 / 未验证</small>
        ${s?.last_import_error?`<p class="lt-warning">导入拒绝：${esc(s.last_import_error.error||'明细不完整')}。继续使用上一个合法快照。</p>`:''}
      </section>
      ${historical?`<p class="lt-warning">${s.snapshot_asof.slice(0,10)>model.date?'按当前题材归属回看历史，不代表当时分类':'题材快照早于所选信号日，归属有待更新'}。当前使用 ${esc(s.snapshot_asof)} 快照。</p>`:''}
      <div class="lt-metrics" aria-label="MA55统计">${[['已保存事件总数',stats.savedEvents],['所选日 '+(model.rule===R1?'R1':'旧V5')+' BUY事件',stats.dayEvents],['BUY去重股票',stats.uniqueStocks],['历史重放 / 实时 / 延迟',`${stats.replayEvents} / ${stats.liveEvents} / ${stats.delayedEvents}`],['WATCH股票',stats.watchStocks],['BUY题材覆盖',s?.snapshot_id?`${stats.labelledStocks} / ${stats.uniqueStocks}`:'待导入']].map(([k,v])=>`<div class="lt-metric"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')}</div>
      <div class="lt-tools"><label>交易日 <select aria-label="结果交易日" data-control="date">${dates.map(date=>`<option ${model.date===date?'selected':''}>${esc(date)}</option>`).join('')}</select></label>
        <label>规则 <select aria-label="信号规则" data-control="rule"><option value="${R1}" ${model.rule===R1?'selected':''}>V5_R1</option><option value="${V5}" ${model.rule===V5?'selected':''}>旧V5（单独查看）</option></select></label>
        <label>来源 <select aria-label="信号来源" data-control="mode">${[['ALL','全部来源'],['LIVE','实时'],['DELAYED','延迟'],['REPLAY','历史重放']].map(([v,t])=>`<option value="${v}" ${model.mode===v?'selected':''}>${t}</option>`).join('')}</select></label>
        <label>归属 <select aria-label="归属范围" data-control="scope">${[['matched','题材命中'],['unlabelled','本快照无标签'],['all','全部信号']].map(([v,t])=>`<option value="${v}" ${model.scope===v?'selected':''}>${t}</option>`).join('')}</select></label>
        <input aria-label="信号股票搜索" data-control="stockQuery" placeholder="股票名称或完整代码" value="${esc(model.stockQuery)}"></div>
      <div class="lt-tabs"><button data-tab="BUY" aria-pressed="${model.tab==='BUY'}">已确认 BUY</button><button data-tab="WATCH" aria-pressed="${model.tab==='WATCH'}">WATCH 观察</button></div>
      ${model.tab==='WATCH'?'<p class="muted">WATCH来自所选日期的已发布状态，不计入BUY；历史观察未提供规则/来源时标为未知，BUY规则和来源筛选仅作用于确认事件。</p>':''}
      <div class="lt-layout"><section class="lt-card lt-column"><h2>1 · 大题材</h2><input aria-label="题材搜索" data-control="themeQuery" placeholder="题材名称 / 来源ID" value="${esc(model.themeQuery)}" style="width:100%">
        <div class="lt-themes"><button class="lt-theme" data-theme="" aria-pressed="${!model.themeId}">全部题材<small>${d.themes.length}个已验证快照条目</small></button>${themeList.map(t=>{const c=themeCounts(t,d.items);return `<button class="lt-theme" data-theme="${esc(t.theme_id)}" aria-pressed="${model.themeId===t.theme_id}">${esc(t.theme_name)}<small>${esc(t.theme_id)} · ${c.hits}命中 / ${c.members}成员</small></button>`;}).join('')}</div>
        ${!d.themes.length?'<p class="lt-empty">暂无题材快照</p>':''}</section>
      <section class="lt-card lt-column lt-branches"><h2>2 · 细分路径</h2>${active?`<p>${esc(active.theme_name)} · ${count.hits}命中股票 / ${count.members}来源成员</p><button class="lt-branch" data-branch="" aria-pressed="${!model.branch}">全部细分</button>${active.nodes.filter(n=>!n.parent_id).map(n=>`<details data-collapse="${esc(n.key)}" ${collapsed.has(n.key)?'':'open'}><summary>${esc(n.name)}</summary>${branchButton(n)}<div class="lt-child">${active.nodes.filter(c=>c.parent_id===n.key).map(branchButton).join('')}</div></details>`).join('')}<button class="lt-branch" data-branch="@unconfirmed" aria-pressed="${model.branch==='@unconfirmed'}">题材成分，细分未确认</button><small>${active.memberships.filter(m=>!m.symbol).length}条关系尚未解析股票身份</small>`:'<p class="muted">选择大题材后展开Level1 / Level2路径。</p>'}</section>
      <section class="lt-card lt-column"><h2>3 · ${model.tab==='BUY'?'已确认股票':'观察股票'} · ${d.selected.length}条${model.tab==='BUY'?'事件':'状态'}</h2>
        ${model.workbench!=='enabled'?'<p class="lt-warning">分钟K工作台未启用或状态未确认；可使用官方个股详情。</p>':''}
        <div class="lt-table"><table><thead><tr><th>股票 / 归属</th><th>确认 / 来源</th><th>支撑 / 防守</th></tr></thead><tbody>${shown.map((item,i)=>{
          const e=item.event,links=d.index.get(item.symbol)||[];
          return `<tr data-event-id="${esc(e?.event_id||'')}"><td>${model.workbench==='enabled'?`<a href="${esc(workbenchLink(item.symbol,e))}">${esc(item.name||item.symbol)}</a>`:`<span>${esc(item.name||item.symbol)}</span>`}<small style="display:block">${esc(item.symbol)}</small><button data-reverse="${esc(item.symbol)}">反查归属</button>${links.map(({theme,member})=>`<span class="lt-path">${esc(theme.theme_name)} · ${esc(member.branch_path.join(' / ')||'细分未确认')}</span>`).join('')||'<span class="lt-path">本快照无已验证标签</span>'}</td>
            <td>${e?`${esc(e.confirmed_at)}<br><span class="lt-flag">${esc(sourceLabel(e.mode))}</span><small style="display:block">${esc(e.rule_version)}</small><details><summary>确认事实</summary><span class="lt-event">${esc(e.event_id)}</span><p>首次保存 ${esc(e.first_seen_at||'未提供')}</p></details>`:`WATCH · ${esc(item.row.bar_end)}<br><small>${esc(item.watch_rule||'历史规则未知')} · 来源未知</small>`}</td>
            <td>${esc(e?.setup_at||'未确认')}<br>防守 ${esc(e?.defense??'—')}<br><button data-preview="${model.page*40+i}" ${ctx.ui?.openStockPreview?'':'disabled'}>官方个股详情</button></td></tr>`;
        }).join('')}</tbody></table>${shown.length?'':`<div class="lt-empty">${model.loading?'正在读取已发布数据…':!s?.snapshot_id&&model.scope==='matched'?'待导入合法题材快照后显示交集':'所选条件下没有已发布'+(model.tab==='BUY'?'确认':'观察')+'。'}</div>`}</div>
        <div class="lt-pager"><button data-action="prev" ${model.page===0?'disabled':''}>上一页</button><small>${model.page+1} / ${pageCount}</small><button data-action="next" ${model.page+1>=pageCount?'disabled':''}>下一页</button></div>
      </section></div>
      <section class="lt-card lt-reverse"><h2>股票反向归属</h2><form class="lt-tools" data-reverse-form><input aria-label="反查股票代码" placeholder="完整代码，如300863.SZ" value="${esc(model.reverse)}"><button>查看全部题材路径</button></form>${model.reverse?`<strong>${esc(model.reverse)}</strong><ul>${reverseLinks.map(({theme,member})=>`<li>${esc(theme.theme_name)} · ${esc(member.branch_path.join(' / ')||'题材成分，细分未确认')}</li>`).join('')||'<li>本快照中没有可核实归属；不按名称猜测。</li>'}</ul>`:''}</section>
      <footer class="lt-footer">题材关系按来源快照展示。全局股票统计按symbol去重，分支命中不累加为全局股票数。筛选只读已发布结果，不改变确认事实。${model.cycle?` 所选日处理 ${model.cycle.processed_symbols} / ${model.cycle.expected_symbols}。`:''}</footer>`;
  }
  async function refresh(date=model.date) {
    const id=++request;model.loading=true;render();
    try {
      const [statusRows,summaryRows,pages]=await Promise.all([readRows(ctx.api,STATUS,1),readRows(ctx.api,'lan_ma55_summary',1),ctx.api.extPagesList()]);
      if(disposed||id!==request) return;
      const status=statusRows.length?JSON.parse(statusRows[0].payload_json):null;
      if(status && status.api_version!==1) throw Error('题材视图版本不匹配');
      const summary=summaryRows.length?JSON.parse(summaryRows[0].payload_json):null;
      if(!summary) throw Error('MA55尚无已发布结果');
      const revision=summary.history_published_at||summary.revision;
      if(revision!==historyRevision) {history={value:null};historyRevision=revision;}
      let themes=model.themes;
      if(status?.snapshot_id!==treeSnapshot) {
        themes=status?.snapshot_id?assembleThemes(status,await readRows(ctx.api,TREE,MAX_PARTS)):[];
      }
      const selectedDate=date||summary.market_date;
      const day=await readDay(ctx.api,selectedDate,summary,history);
      if(disposed||id!==request) return;
      Object.assign(model,{status,summary,date:selectedDate,themes,rows:day.rows,cycle:day.cycle,error:'',
        workbench:pages.some(p=>p.enabled&&!p.error&&(p.path==='/lan-kline'||p.managed_by==='lan.kline'))?'enabled':'disabled'});
      treeSnapshot=status?.snapshot_id;
      if(model.themeId&&!themes.some(t=>t.theme_id===model.themeId)) {model.themeId='';model.branch='';}
    } catch(e) {if(!disposed&&id===request) model.error=e.message+'；保留上次有效结果。';}
    finally {if(!disposed&&id===request) {model.loading=false;render();}}
  }
  function click(e) {
    const t=e.target.closest('button');if(!t) return;
    if(t.dataset.theme!==undefined) {model.themeId=t.dataset.theme;model.branch='';model.scope='matched';model.page=0;render();}
    else if(t.dataset.branch!==undefined) {model.branch=t.dataset.branch;model.page=0;render();}
    else if(t.dataset.tab) {model.tab=t.dataset.tab;model.page=0;render();}
    else if(t.dataset.reverse) {model.reverse=t.dataset.reverse;render();}
    else if(t.dataset.preview!==undefined) {const item=data().selected[Number(t.dataset.preview)];ctx.ui?.openStockPreview({symbol:item.symbol,name:item.name});}
    else if(t.dataset.action==='refresh') void refresh();
    else if(t.dataset.action==='prev') {model.page--;render();}
    else if(t.dataset.action==='next') {model.page++;render();}
  }
  function change(e) {
    const control=e.target.dataset.control;
    if(!control) return;
    if(control==='date') {model.page=0;void refresh(e.target.value);return;}
    model[control]=e.target.value;model.page=0;
    if(control==='scope'&&model.scope==='unlabelled') {model.themeId='';model.branch='';}
    render();
  }
  function input(e) {
    if(!['stockQuery','themeQuery'].includes(e.target.dataset.control)) return;
    const control=e.target.dataset.control,position=e.target.selectionStart;
    model[control]=e.target.value;model.page=0;render();
    const field=root.querySelector(`[data-control="${control}"]`);field.focus();field.setSelectionRange(position,position);
  }
  function toggle(e) {const key=e.target.dataset?.collapse;if(key) {if(e.target.open)collapsed.delete(key);else collapsed.add(key);}}
  function submit(e) {if(e.target.matches('[data-reverse-form]')) {e.preventDefault();model.reverse=e.target.querySelector('input').value.trim().toUpperCase();render();}}
  root.addEventListener('click',click);root.addEventListener('change',change);root.addEventListener('input',input);root.addEventListener('toggle',toggle,true);root.addEventListener('submit',submit);
  void refresh();
  return ()=>{disposed=true;request++;root.remove();};
}
