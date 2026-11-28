import { icon } from './presentation.mjs';
import { PERIOD_LABEL, TIMEFRAMES } from './workbench_data.mjs';
import { matrixLegend } from './stock_matrix.mjs';

export const LAYOUT = `<header class="mr-header">
  <div class="mr-heading"><h1>MA55 板块观察</h1><p class="mr-meta" data-role="market"></p></div>
  <div class="mr-actions">
    <span class="mr-task-chip" data-role="task-chip"></span>
    <button class="mr-icon-button" data-act="refresh" title="刷新展示" aria-label="刷新展示">${icon('refresh')}</button>
    <button class="mr-primary" data-act="manage" aria-haspopup="dialog">${icon('settings')}关注管理</button>
  </div>
</header>
<div class="mr-metrics" data-role="metrics" aria-label="监控概况"></div>
<section class="mr-panel mr-runtime" aria-label="运行状态">
  <div class="mr-task-line">
    <div class="mr-task" data-role="task"><span class="mr-glyph" data-role="task-icon"></span>
      <strong data-role="task-label" role="status"></strong><small data-role="task-note"></small></div>
    <dl class="mr-task-times">
      <div><dt>15m 目标</dt><dd data-role="target"></dd></div>
      <div><dt>结果更新</dt><dd data-role="result-time"></dd></div>
    </dl>
  </div>
  <details class="mr-run-details">
    <summary class="mr-progress-line">
      <span>15m 覆盖</span><b data-role="coverage"></b>
      <span data-role="progress" class="mr-progress" role="progressbar" aria-label="15m 就绪覆盖率" hidden><span></span></span>
      <span class="mr-details-label">运行详情${icon('chevron')}</span>
    </summary>
    <div class="mr-run-body">
      <div class="mr-period-coverage" data-role="period-coverage"></div>
      <dl class="mr-facts" data-role="runtime-facts"></dl>
      <div class="mr-block-groups" data-role="blocking"></div>
    </div>
  </details>
  <p class="mr-error" role="status" data-role="notice"></p>
</section>
<div class="mr-columns">
  <aside class="mr-panel mr-sidebar" data-role="rail">
    <header class="mr-panel-head"><h2>当前关注</h2>
      <button class="mr-icon-button mr-rail-toggle" data-act="toggle-rail" aria-expanded="false"
        aria-label="展开当前关注" title="展开当前关注">${icon('chevron')}</button></header>
    <div class="mr-rail-body">
      <label class="mr-search">${icon('search')}<input data-role="scope-search" aria-label="搜索已保存关注" placeholder="搜索板块或组"></label>
      <nav class="mr-scopes" aria-label="已保存的板块和组" data-role="tabs"></nav>
      <button class="mr-rail-manage" data-act="manage" aria-haspopup="dialog">${icon('plus')}管理关注</button>
    </div>
  </aside>
  <section class="mr-panel mr-matrix" aria-label="关注股票" data-role="matrix">
    <section class="mr-activation" aria-label="最新激活">
      <div class="mr-activation-head"><h3>最新激活</h3>
        <div class="mr-segments mr-activation-periods" role="group" aria-label="最新激活周期" data-role="result-periods"></div>
      </div>
      <div class="mr-activation-controls" data-role="result-controls"></div>
      <div class="mr-activation-list" data-role="result-list"></div>
    </section>
    <div class="mr-matrix-top"><header class="mr-panel-head"><h2>六周期观察</h2><span class="mr-meta" data-role="row-count"></span></header>
      <div class="mr-tools">
        <label class="mr-search">${icon('search')}<input data-role="search" aria-label="搜索股票" placeholder="名称或代码"></label>
        <label class="mr-filter">矩阵状态 <select data-role="filter"><option value="all">全部</option><option value="ACTIVATED">激活</option>
          <option value="INACTIVE">未激活</option><option value="PENDING">待算</option><option value="MISSING">缺数据</option></select></label>
        <label class="mr-filter">联动周期 <select data-role="filter-period">${TIMEFRAMES.map(tf =>
          `<option value="${tf}">${PERIOD_LABEL[tf]}</option>`).join('')}</select></label>
        <label class="mr-filter">排序 <select data-role="order"><option value="candidate">最近激活</option>
          <option value="symbol">股票代码</option><option value="change">涨跌幅</option></select></label>
        <button type="button" data-act="subgroups" aria-pressed="false" hidden>按下级行业分组</button>
        <button type="button" data-act="clear-reason" hidden title="清除原因筛选"></button>
      </div><div class="mr-state-legend" aria-label="状态图例">${matrixLegend}</div></div>
    <div class="mr-table mr-focus-list" data-role="table-wrap" tabindex="0" aria-label="六周期股票矩阵"><table>
      <thead><tr><th scope="col">股票</th><th scope="col">完成价 / 涨跌</th>${TIMEFRAMES.map(tf =>
        `<th scope="col" data-column="${tf}">${PERIOD_LABEL[tf]}</th>`).join('')}<th scope="col">最近上方 MA55</th></tr></thead>
      <tbody data-role="stocks"></tbody>
    </table></div>
  </section>
</div>
<section class="mr-panel mr-chart-panel" data-role="chart-panel" aria-label="所选股票图表" hidden>
  <div class="mr-panel-head mr-chart-heading"><div><h2 data-role="selection"></h2><p class="mr-meta" data-role="membership"></p>
    <p class="mr-meta" data-role="nav-context"></p></div>
    <div class="mr-actions">
      <button data-act="previous" class="mr-icon-button" title="上一只" aria-label="上一只">${icon('arrowLeft')}</button>
      <button data-act="next" class="mr-icon-button" title="下一只" aria-label="下一只">${icon('arrowRight')}</button>
      <button data-act="preview">${icon('external')}官方详情</button>
    </div>
  </div>
  <div class="mr-tools"><div class="mr-segments" data-role="periods" role="group" aria-label="图表周期"></div>
    <label class="mr-check"><input data-role="show-levels" type="checkbox">上方55线</label>
    <button data-act="locate" hidden>${icon('target')}定位确认</button>
    <span class="mr-anchor" data-role="anchor"></span>
  </div>
  <p class="mr-meta" data-role="chart-state" role="status"></p>
  <div class="mr-stock-diagnostic" data-role="stock-diagnostic"></div>
  <div class="mr-chart-readout"><p class="mr-readout" data-role="readout"></p>
    <div class="mr-ma-legend" role="group" aria-label="均线显示" data-role="ma-legend"></div></div>
  <div class="mr-chart-body"><div class="mr-canvas" data-role="chart" hidden></div><div data-role="levels"></div></div>
</section>
<div class="mr-secondary">
  <details class="mr-panel mr-fold" data-role="archive"><summary>${icon('archive')}冻结 BUY 历史<span>${icon('chevron')}</span></summary>
    <div class="mr-detail-rows" data-role="archive-list"></div>
    <div class="mr-pagination"><span class="mr-meta" data-role="history-page"></span>
      <button class="mr-icon-button" data-act="history-prev" title="历史上一页" aria-label="历史上一页">${icon('arrowLeft')}</button>
      <button class="mr-icon-button" data-act="history-next" title="历史下一页" aria-label="历史下一页">${icon('arrowRight')}</button></div>
  </details>
  <details class="mr-panel mr-fold"><summary>${icon('activity')}数据诊断<span>${icon('chevron')}</span></summary>
    <dl class="mr-facts" data-role="system-facts"></dl>
    <dl class="mr-facts" data-role="source-facts"></dl>
    <details class="mr-dev"><summary>开发者明细${icon('chevron')}</summary><pre data-role="diagnostics"></pre></details>
  </details>
</div>
<dialog class="mr-dialog" data-role="manager" aria-labelledby="mr-manager-title">
  <div class="mr-dialog-body"><header class="mr-dialog-head"><h2 id="mr-manager-title">关注管理</h2>
    <button class="mr-icon-button" data-act="close-manager" title="关闭关注管理" aria-label="关闭关注管理">${icon('close')}</button></header>
    <div data-role="focus"></div>
  </div>
</dialog>`;
