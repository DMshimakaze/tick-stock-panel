export const CSS = `
.mr {
  --mr-surface: hsl(var(--surface));
  --mr-line: hsl(var(--border));
  --mr-ink: hsl(var(--fg-primary));
  --mr-muted: hsl(var(--fg-secondary));
  --mr-soft: hsl(var(--elevated));
  --mr-blue: #225cb0;
  --mr-violet: #6842a5;
  --mr-teal: #167365;
  --mr-amber: #955114;
  --mr-red: #b63c35;
  --mr-mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
  container-type: inline-size;
  min-width: 0;
  max-width: 1640px;
  margin-inline: auto;
  padding: 20px 24px 28px;
  background: radial-gradient(ellipse at top left, #3b82f60e, transparent 46%),
    radial-gradient(ellipse at top right, #8b5cf60a, transparent 45%);
  color: var(--mr-ink);
  font-size: 12px;
  line-height: 1.45;
  font-variant-numeric: tabular-nums;
}
.dark .mr {
  --mr-blue: #91bfff;
  --mr-violet: #c4a5fc;
  --mr-teal: #77d9c6;
  --mr-amber: #efb972;
  --mr-red: #ff9c95;
}
.mr *, .mr *::before, .mr *::after { box-sizing: border-box; }
.mr [hidden], .mr .mr-dialog:not([open]) { display: none !important; }
.mr :is(h1,h2,h3,h4,p,dl,dd) { margin: 0; }
.mr h1 { font-size: 20px; line-height: 1.3; font-weight: 680; }
.mr :is(h2,h3,h4) { font-size: 13px; font-weight: 650; }
.mr .mr-meta, .mr small { font-size: 11px; color: var(--mr-muted); }
.mr .mr-icon { width: 15px; height: 15px; flex: 0 0 15px; vertical-align: middle; }
.mr :where(button,select,input:not([type=checkbox])) {
  min-height: 30px;
  max-width: 100%;
  padding: 4px 8px;
  border: 1px solid var(--mr-line);
  border-radius: 7px;
  background: var(--mr-surface);
  color: inherit;
  font: inherit;
}
.mr button { display: inline-flex; align-items: center; justify-content: center; gap: 5px; cursor: pointer; white-space: nowrap; }
.mr button:not(:disabled):hover { background: var(--mr-soft); border-color: color-mix(in srgb, var(--mr-blue), var(--mr-line) 70%); }
.mr button:disabled { opacity: .5; cursor: default; }
.mr :is(button,input,select,summary,[tabindex]):focus-visible { outline: 2px solid var(--mr-blue); outline-offset: -2px; }
.mr .mr-primary { background: color-mix(in srgb, var(--mr-blue) 9%, var(--mr-surface)); color: var(--mr-blue); }
.mr .mr-icon-button { width: 28px; min-width: 28px; min-height: 28px; padding: 0; color: var(--mr-muted); }
.mr .mr-actions, .mr .mr-tools, .mr .mr-panel-head { display: flex; align-items: center; gap: 6px; min-width: 0; }
.mr .mr-tools, .mr .mr-actions { flex-wrap: wrap; }
.mr .mr-panel-head { justify-content: space-between; }
.mr .mr-search {
  display: flex; align-items: center; gap: 3px; min-width: 0; padding-left: 8px;
  border: 1px solid var(--mr-line); border-radius: 8px; background: var(--mr-soft); color: var(--mr-muted);
}
.mr .mr-search:focus-within { border-color: var(--mr-blue); }
.mr .mr-search input:not([type=checkbox]) { width: 100%; min-width: 0; min-height: 29px; border: 0; padding: 4px 7px 4px 3px; background: transparent; color: var(--mr-ink); }
.mr .mr-search input:focus-visible { outline: none; }
.mr .mr-panel { min-width: 0; border: 1px solid var(--mr-line); border-radius: 13px; background: var(--mr-surface); }
.mr .mr-segments { display: flex; flex-wrap: wrap; padding: 2px; gap: 2px; width: fit-content; border: 1px solid var(--mr-line); border-radius: 8px; background: var(--mr-soft); }
.mr .mr-segments button { border-color: transparent; background: transparent; color: var(--mr-muted); min-height: 26px; }
.mr .mr-segments button[aria-pressed=true] { background: var(--mr-surface); border-color: var(--mr-line); color: var(--mr-blue); }
.mr .mr-error { padding-top: 6px; color: var(--mr-red); font-size: 11px; overflow-wrap: anywhere; }
.mr .mr-error:empty { display: none; }
.mr .mr-code { color: var(--mr-muted); font: 11px/1.3 var(--mr-mono); }
.mr .mr-count { font-family: var(--mr-mono); font-size: 11px; font-weight: 500; color: var(--mr-muted); text-align: right; white-space: nowrap; }
.mr .mr-empty { color: var(--mr-muted); font-size: 12px; text-align: center; padding: 22px 10px; }
.mr .mr-note, .mr .mr-task-chip { padding: 3px 7px; border-radius: 6px; color: var(--mr-tone, var(--mr-muted)); background: color-mix(in srgb, var(--mr-tone, var(--mr-muted)) 9%, transparent); }
.mr :is(.mr-glyph,.mr-kind) { display: inline-grid; place-items: center; flex: none; width: 24px; height: 24px; border-radius: 7px; color: var(--mr-tone, var(--mr-muted)); background: color-mix(in srgb, var(--mr-tone, var(--mr-muted)) 10%, transparent); }
.mr :is([data-tone=blue],[data-kind=concept]) { --mr-tone: var(--mr-blue); }
.mr :is([data-tone=violet],[data-kind=theme]) { --mr-tone: var(--mr-violet); }
.mr :is([data-tone=teal],[data-kind=groups]) { --mr-tone: var(--mr-teal); }
.mr :is([data-tone=amber],[data-kind=industry]) { --mr-tone: var(--mr-amber); }
.mr [data-tone=red] { --mr-tone: var(--mr-red); }
.mr summary { cursor: pointer; list-style: none; }
.mr summary::-webkit-details-marker { display: none; }
.mr details[open] > summary .mr-chevron, .mr details[open] > summary > .mr-icon:last-child { transform: rotate(180deg); }
.mr table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; }
.mr :is(th,td) { text-align: left; padding: 6px 9px; border-bottom: 1px solid var(--mr-line); }
.mr th { color: var(--mr-muted); font-weight: 550; font-size: 11px; background: var(--mr-soft); }
.mr tbody tr:last-child td { border-bottom: 0; }
.mr .mr-stock-name { border-color: transparent; background: transparent; min-height: 22px; padding: 1px 4px; margin-left: -4px; font-weight: 650; }
.mr .mr-pagination { display: flex; justify-content: flex-end; align-items: center; gap: 5px; min-height: 29px; margin-top: 5px; }
.mr .mr-pagination span { margin-right: auto; }
@media (max-width: 767px) {
  .mr { padding: 11px 12px 22px; }
  .mr .mr-header { padding-inline-start: 51px; }
}
`;

export const WORKBENCH_CSS = `
.mr .mr-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; min-height: 40px; }
.mr .mr-heading > p { margin-top: 1px; }
.mr .mr-task-chip { display: inline-flex; gap: 4px; align-items: center; white-space: nowrap; font-size: 11px; }
.mr .mr-task-chip .mr-icon { width: 13px; height: 13px; flex-basis: 13px; }
.mr .mr-metrics { display: grid; gap: 9px; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 9px; }
.mr .mr-kpi { min-width: 0; border: 1px solid var(--mr-line); border-radius: 12px; padding: 7px 11px; background: var(--mr-surface); }
.mr .mr-kpi-label { display: flex; align-items: center; justify-content: space-between; color: var(--mr-muted); }
.mr .mr-kpi-label .mr-glyph { width: 22px; height: 22px; }
.mr .mr-kpi strong { display: block; font: 650 19px/1.16 var(--mr-mono); color: var(--mr-tone, var(--mr-ink)); margin-bottom: 1px; }
.mr .mr-kpi small { display: block; min-height: 16px; }
.mr .mr-runtime { margin-bottom: 10px; padding: 7px 11px; }
.mr .mr-task-line, .mr .mr-task, .mr .mr-task-times, .mr .mr-task-times > div { display: flex; align-items: center; gap: 6px; }
.mr .mr-task-line { justify-content: space-between; flex-wrap: wrap; }
.mr .mr-task { gap: 7px; }
.mr .mr-task strong { color: var(--mr-tone, var(--mr-muted)); font-size: 13px; }
.mr .mr-task-times { gap: 13px; flex-wrap: wrap; font-size: 11px; }
.mr .mr-task-times dt { color: var(--mr-muted); }
.mr .mr-task-times dd { font-family: var(--mr-mono); }
.mr .mr-task[data-running=true] .mr-glyph { animation: mr-active 1.7s ease-in-out infinite alternate; }
@keyframes mr-active { to { opacity: .55; } }
@media (prefers-reduced-motion: reduce) { .mr .mr-task[data-running=true] .mr-glyph { animation: none; } }
.mr .mr-progress-line { display: flex; gap: 8px; align-items: center; min-height: 23px; margin-top: 3px; color: var(--mr-muted); font-size: 11px; }
.mr .mr-progress-line b { color: var(--mr-ink); font-family: var(--mr-mono); font-weight: 550; }
.mr .mr-progress { flex: 1; min-width: 20px; height: 4px; background: var(--mr-soft); border-radius: 4px; overflow: hidden; }
.mr .mr-progress > span { display: block; height: 100%; background: var(--mr-teal); border-radius: inherit; }
.mr .mr-details-label { display: inline-flex; align-items: center; gap: 2px; margin-left: auto; color: var(--mr-blue); white-space: nowrap; }
.mr .mr-run-details[open] .mr-details-label .mr-icon { transform: rotate(180deg); }
.mr .mr-run-body { border-top: 1px solid var(--mr-line); padding-top: 8px; }
.mr .mr-facts, .mr .mr-period-coverage { display: grid; gap: 5px; grid-template-columns: repeat(auto-fit,minmax(90px,1fr)); }
.mr .mr-facts > div, .mr .mr-period-coverage > div { min-width: 0; padding: 5px 7px; border-radius: 7px; background: var(--mr-soft); overflow-wrap: anywhere; }
.mr .mr-facts dt, .mr .mr-period-coverage span { display: block; color: var(--mr-muted); font-size: 11px; }
.mr .mr-facts dd, .mr .mr-period-coverage b { font-family: var(--mr-mono); }
.mr .mr-run-body .mr-facts { margin-top: 6px; }
.mr .mr-block-groups:not(:empty) { display: grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr)); gap: 7px; margin-top: 7px; }
.mr .mr-block-groups ul, .mr .mr-lookup-group ul { list-style: none; margin: 0; padding: 0; }
.mr .mr-block-groups li { display: grid; gap: 1px; margin-bottom: 3px; color: var(--mr-muted); font-size: 11px; }
.mr .mr-block-groups li div { display: flex; gap: 3px; flex-wrap: wrap; }
.mr .mr-block-groups li button { min-height: 21px; padding: 1px 5px; font-size: 10px; }
.mr .mr-columns { display: grid; grid-template-columns: clamp(240px, 23%, 288px) minmax(0,1fr); gap: 10px; align-items: start; }
.mr .mr-sidebar { padding: 9px; }
.mr .mr-sidebar h2 { padding-left: 1px; }
.mr .mr-rail-toggle { display: none; }
.mr .mr-rail-body { margin-top: 7px; }
.mr .mr-scopes { display: grid; gap: 3px; min-width: 0; max-height: 340px; padding-block: 5px; overflow: auto; }
.mr .mr-scope-row { display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 3px; align-items: center; }
.mr .mr-scope { grid-column: 1/-1; justify-content: start; gap: 6px; width: 100%; text-align: left; background: transparent; border-color: transparent; padding: 3px 5px; }
.mr .mr-scope-row:has([data-inspect]) .mr-scope { grid-column: 1; }
.mr .mr-scope[aria-pressed=true] { color: var(--mr-blue); background: color-mix(in srgb, var(--mr-blue) 9%, transparent); border-color: color-mix(in srgb, var(--mr-blue) 20%, var(--mr-line)); }
.mr .mr-scope b, .mr .mr-board-name b, .mr .mr-stock-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mr .mr-scope .mr-count { margin-left: auto; }
.mr .mr-group-toggle[aria-expanded=false] .mr-icon { transform: rotate(-90deg); }
.mr .mr-rail-manage { width: 100%; justify-content: start; color: var(--mr-blue); }
.mr .mr-matrix { overflow: hidden; }
.mr .mr-matrix-top { padding: 8px 10px 7px; }
.mr .mr-activation { padding: 7px 10px; border-bottom: 1px solid var(--mr-line); min-width: 0; }
.mr .mr-activation-head, .mr .mr-activation-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 5px 8px; }
.mr .mr-activation-head h3 { margin: 0; font-size: 12px; white-space: nowrap; }
.mr .mr-activation-periods { flex: 1 1 300px; display: grid; grid-template-columns: repeat(auto-fit, minmax(48px, 1fr)); min-width: 0; }
.mr .mr-activation-periods button { font-size: 11px; padding: 2px 4px; white-space: nowrap; }
.mr .mr-activation-periods b { font: 600 11px/1 var(--mr-mono); }
.mr .mr-activation-controls { margin-top: 4px; }
.mr .mr-activation-controls button { min-height: 22px; font-size: 11px; padding: 1px 5px; }
.mr .mr-activation-list { max-height: 128px; overflow: auto; margin-top: 4px; }
.mr .mr-activation-list[data-expanded=true] { max-height: min(58vh, 490px); }
.mr .mr-group-toggle { display: flex; gap: 6px; justify-content: flex-start; align-items: center; width: 100%; min-height: 23px;
  background: var(--mr-soft); border-color: transparent; text-align: left; padding: 2px 5px; color: var(--mr-ink); font-size: 11px; }
.mr .mr-group-toggle .mr-count { margin-left: auto; white-space: nowrap; }
.mr .mr-group-toggle .mr-icon { width: 11px; height: 11px; flex: none; }
.mr .mr-activation-item { display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: center; column-gap: 3px; }
.mr .mr-activation-entry { width: 100%; justify-content: start; gap: 5px; min-height: 23px; padding: 2px 5px;
  border-color: transparent; background: transparent; font-size: 11px; }
.mr .mr-activation-entry b { min-width: 0; margin-right: auto; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.mr .mr-activation-entry time, .mr .mr-activation-entry small { color: var(--mr-muted); font-family: var(--mr-mono); white-space: nowrap; }
.mr .mr-activation-entry[aria-current=true], .mr .mr-activation-more [aria-current=true] { color: var(--mr-blue); border-color: var(--mr-blue); }
.mr .mr-event-info { width: auto; min-width: 23px; min-height: 22px; padding: 0 3px; gap: 2px; color: var(--mr-muted); }
.mr .mr-event-info[aria-expanded=true] .mr-icon { transform: rotate(180deg); }
.mr .mr-activation-more { grid-column: 1/-1; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin: 0 4px 3px; padding: 3px 4px; }
.mr .mr-activation-more small { color: var(--mr-muted); }
.mr .mr-activation-more button { min-height: 20px; font-size: 10px; }
.mr .mr-matrix-top .mr-tools { margin-top: 6px; }
.mr .mr-matrix-top .mr-search { flex: 1 1 135px; max-width: 290px; }
.mr .mr-filter { display: inline-flex; align-items: center; gap: 4px; color: var(--mr-muted); white-space: nowrap; font-size: 11px; }
.mr .mr-filter select { color: var(--mr-ink); padding: 3px 5px; }
.mr .mr-state-legend { display: flex; flex-wrap: wrap; gap: 3px 9px; margin-top: 4px; }
.mr .mr-state-legend span { display: inline-flex; gap: 3px; align-items: center; color: var(--mr-muted); font-size: 10px; }
.mr .mr-state-legend i { width: 12px; display: inline-grid; place-items: center; font-style: normal; }
.mr .mr-state-legend svg, .mr .mr-frame svg { width: 13px; height: 13px; }
.mr .mr-focus-list { overflow: auto; max-height: clamp(280px, 47vh, 430px); border-top: 1px solid var(--mr-line); }
.mr .mr-focus-list table { min-width: 665px; table-layout: fixed; }
.mr .mr-focus-list thead th { position: sticky; top: 0; z-index: 1; }
.mr .mr-focus-list :is(th,td) { white-space: nowrap; padding: 3px 6px; }
.mr .mr-focus-list .mr-matrix-group th { width: auto; height: 25px; padding: 1px 4px; background: color-mix(in srgb, var(--mr-blue) 5%, var(--mr-surface)); }
.mr .mr-matrix-group-actions { display: flex; align-items: center; gap: 4px; position: sticky; left: 4px; max-width: min(100%, 92cqw); }
.mr .mr-matrix-group-actions > [data-industry-detail] { flex: none; min-height: 23px; padding: 2px 6px; font-size: 11px; }
.mr .mr-matrix-group[data-level="2"] .mr-matrix-group-actions { padding-left: 13px; }
.mr .mr-matrix-group[data-level="3"] .mr-matrix-group-actions { padding-left: 26px; }
.mr .mr-focus-list th { height: 28px; }
.mr .mr-focus-list td { height: 37px; }
.mr .mr-focus-list :is(th,td):first-child { width: 18%; padding-left: 10px; }
.mr .mr-focus-list :is(th,td):nth-child(2) { width: 13%; }
.mr .mr-focus-list :is(th,td):last-child { width: 15%; padding-right: 10px; }
.mr .mr-focus-list :is(th,td):is(:nth-child(2),:last-child) { text-align: right; }
.mr .mr-focus-list :is(th,td)[data-column] { text-align: center; }
.mr .mr-focus-list td:only-child { text-align: center; padding: 23px 6px; }
.mr .mr-focus-list .mr-stock-name { display: block; min-height: 17px; line-height: 1.2; }
.mr .mr-focus-list .mr-code { display: block; }
.mr .mr-focus-list :is(tr:hover,tr.selected) { background: color-mix(in srgb, var(--mr-blue) 7%, var(--mr-surface)); }
.mr .mr-price { font-family: var(--mr-mono); }
.mr .mr-price small { font-family: inherit; font-size: 10px; }
.mr .mr-frame { padding: 2px 5px; min-width: 25px; min-height: 23px; font-size: 11px; border-color: transparent; background: transparent; color: var(--mr-muted); }
.mr .mr-frame:not([data-state="INACTIVE"]) { background: color-mix(in srgb, currentColor 8%, transparent); }
.mr [data-state="ACTIVATED"] :is(i,span), .mr .mr-frame[data-state="ACTIVATED"] { color: var(--mr-violet); }
.mr [data-state="PENDING"] :is(i,span), .mr .mr-frame[data-state="PENDING"] { color: var(--mr-amber); }
.mr [data-state="MISSING"] :is(i,span), .mr .mr-frame[data-state="MISSING"] { color: var(--mr-red); }
.mr .mr-matrix[data-period-lens="15m"] [data-column="15m"],
.mr .mr-matrix[data-period-lens="30m"] [data-column="30m"],
.mr .mr-matrix[data-period-lens="60m"] [data-column="60m"],
.mr .mr-matrix[data-period-lens="120m"] [data-column="120m"],
.mr .mr-matrix[data-period-lens="daily"] [data-column="daily"],
.mr .mr-matrix[data-period-lens="weekly"] [data-column="weekly"] { box-shadow: inset 0 -1px 0 color-mix(in srgb, var(--mr-blue) 50%, var(--mr-line)); }
.mr .mr-rise { color: hsl(var(--bull)); }
.mr .mr-fall { color: hsl(var(--bear)); }
.mr .mr-chart-panel { margin-top: 10px; padding: 10px; }
.mr .mr-chart-heading { margin-bottom: 5px; flex-wrap: wrap; }
.mr .mr-check { display: inline-flex; align-items: center; gap: 4px; }
.mr .mr-chart-panel .mr-meta { margin-top: 3px; }
.mr .mr-anchor { color: var(--mr-blue); font: 11px/1.4 var(--mr-mono); }
.mr .mr-stock-diagnostic:not(:empty) { margin: 5px 0; border: 1px solid var(--mr-line); border-radius: 7px; padding: 6px; }
.mr .mr-readout { color: var(--mr-muted); font: 11px/1.4 var(--mr-mono); min-height: 1.4em; }
.mr .mr-chart-readout, .mr .mr-ma-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 3px 6px; }
.mr .mr-ma-legend button { display: inline-flex; align-items: center; gap: 3px; min-height: 21px; padding: 1px 4px;
  color: var(--mr-ink); font: 11px/1.3 var(--mr-mono); }
.mr .mr-ma-legend button::before { content: ''; width: 7px; height: 2px; background: var(--ma-color); }
.mr .mr-ma-legend button[aria-pressed=false] { opacity: .5; }
.mr .mr-chart-body { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,220px); gap: 8px; }
.mr .mr-chart-body:has(>[data-role=levels]:empty,>.mr-canvas[hidden]) { grid-template-columns: minmax(0,1fr); }
.mr [data-role=levels]:empty { display: none; }
.mr .mr-canvas { min-width: 0; height: 355px; color: var(--mr-muted); }
.mr .mr-levels { border: 1px solid var(--mr-line); border-radius: 9px; padding: 7px; }
.mr .mr-levels > div { display: grid; grid-template-columns: 44px 1fr; column-gap: 6px; border-top: 1px solid var(--mr-line); padding: 3px; }
.mr .mr-levels small { grid-column: 1/-1; }
.mr .mr-levels span { text-align: right; font-family: var(--mr-mono); }
.mr .mr-secondary { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 9px; margin-top: 9px; align-items: start; }
.mr .mr-fold { padding: 0 9px; }
.mr .mr-fold > summary { display: flex; align-items: center; gap: 6px; min-height: 36px; font-weight: 600; }
.mr .mr-fold > summary > span { margin-left: auto; color: var(--mr-muted); }
.mr .mr-fold[open] > summary > span { transform: rotate(180deg); }
.mr .mr-fold[open] { padding-bottom: 7px; }
.mr .mr-fold .mr-facts { margin-bottom: 5px; }
.mr .mr-dev { padding-top: 4px; border-top: 1px solid var(--mr-line); }
.mr .mr-dev summary { display: flex; align-items: center; justify-content: space-between; color: var(--mr-muted); font-size: 11px; }
.mr .mr-dev pre { max-height: 230px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 11px/1.4 var(--mr-mono); }

.mr .mr-dialog {
  position: fixed;
  inset: 0;
  width: min(960px, calc(100vw - 24px));
  max-width: none;
  max-height: calc(100dvh - 30px);
  margin: auto;
  padding: 0;
  overflow: auto;
  border: 1px solid var(--mr-line);
  border-radius: 15px;
  background: var(--mr-surface);
  color: var(--mr-ink);
  box-shadow: 0 12px 46px #0002;
}
.mr .mr-dialog::backdrop { background: #11182770; }
.mr .mr-dialog-body { container-type: inline-size; padding: 0 11px 10px; }
.mr .mr-dialog-head { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 8px 0 6px; background: var(--mr-surface); }
.mr .mr-dialog-head h2 { font-size: 16px; }
.mr .mr-manager-grid { display: grid; grid-template-columns: minmax(0,1fr); gap: 8px; align-items: start; }
.mr .mr-manager-grid:has(.mr-taxonomy-detail:not([hidden])) { grid-template-columns: minmax(0,5fr) minmax(0,6fr); }
.mr :is(.mr-catalog,.mr-taxonomy-detail,.mr-draft,.mr-manage-section) { min-width: 0; padding: 8px; border: 1px solid var(--mr-line); border-radius: 10px; }
.mr .mr-catalog .mr-tools { margin: 5px 0; }
.mr .mr-catalog .mr-search { flex: 1 1 170px; }
.mr .mr-inventory { display: flex; flex-wrap: wrap; gap: 2px 8px; align-items: center; color: var(--mr-muted); font-size: 11px; margin-top: 3px; }
.mr .mr-inventory .mr-note { padding: 0 3px; }
.mr .mr-directions { margin: 4px 0; color: var(--mr-muted); }
.mr :is(.mr-directions,.mr-manage-section,.mr-alias) > summary { display: flex; align-items: center; justify-content: space-between; gap: 5px; font-weight: 600; }
.mr :is(.mr-taxonomy-tree,.mr-taxonomy-board-list) { max-height: 225px; overflow: auto; }
.mr .mr-taxonomy-tree:not([hidden]) + .mr-taxonomy-board-list { max-height: 118px; margin-top: 4px; border-top: 1px solid var(--mr-line); }
.mr .mr-taxonomy-board {
  display: grid; grid-template-columns: 24px minmax(0,1fr) auto 28px 28px;
  align-items: center; gap: 4px; min-height: 36px; padding: 2px 4px; border-bottom: 1px solid var(--mr-line);
}
.mr .mr-taxonomy-tree .mr-taxonomy-board { grid-template-columns: 14px 24px minmax(0,1fr) auto 28px 28px; }
.mr .mr-taxonomy-tree .mr-taxonomy-board:not(summary) > .mr-kind { grid-column: 2; }
.mr .mr-tree-chevron .mr-icon { transform: rotate(-90deg); }
.mr .mr-taxonomy-tree details[open] > summary .mr-icon { transform: none; }
.mr .mr-tree-children { margin-inline-start: 9px; padding-inline-start: 3px; border-left: 1px solid var(--mr-line); }
.mr .mr-taxonomy-board:hover { background: var(--mr-soft); }
.mr .mr-board-name { min-width: 0; }
.mr .mr-board-name :is(b,small) { display: block; }
.mr .mr-board-name small { font-size: 10px; }
.mr .mr-board-name em { color: var(--mr-amber); font-style: normal; }
.mr :is(.mr-draft,.mr-manage-section) { margin-top: 7px; }
.mr .mr-chips, .mr .mr-delta { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
.mr .mr-chips { padding: 4px 0; }
.mr .mr-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 4px; border: 1px solid var(--mr-line); border-radius: 7px; background: var(--mr-soft); }
.mr .mr-chip .mr-kind, .mr .mr-chip .mr-icon-button { width: 21px; min-width: 21px; height: 21px; min-height: 21px; }
.mr .mr-chip .mr-icon-button { background: transparent; border-color: transparent; }
.mr .mr-draft-footer { display: flex; flex-wrap: wrap; gap: 5px; justify-content: space-between; align-items: center; }
.mr .mr-manage-section .mr-tools, .mr .mr-alias .mr-tools { margin-top: 5px; }
.mr .mr-group-entry { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin: 5px 0; }
.mr :is(.mr-group-entry > span, .mr-group-match) { display: flex; align-items: center; gap: 4px; min-width: 0; }
.mr .mr-group-entry > span { flex: 1 1 auto; }
.mr .mr-group-match { width: 100%; justify-content: flex-start; border-color: transparent; padding: 3px; }
.mr .mr-group-match .mr-icon:last-child { margin-left: auto; }
.mr .mr-detail-heading { display: flex; align-items: center; gap: 5px; min-width: 0; }
.mr .mr-breadcrumb { display: flex; flex-wrap: wrap; gap: 3px; font-size: 11px; margin: 5px 0; color: var(--mr-amber); }
.mr .mr-breadcrumb span { padding: 2px 5px; border-radius: 5px; background: color-mix(in srgb, var(--mr-amber) 9%, transparent); }
.mr .mr-detail-facts { margin: 5px 0; }
.mr .mr-detail-rows { max-height: 301px; overflow: auto; border: 1px solid var(--mr-line); border-radius: 8px; }
.mr .mr-members { min-width: max-content; }
.mr .mr-taxonomy-detail .mr-members { min-width: 0; }
.mr .mr-members th { position: sticky; top: 0; }
.mr .mr-members td:nth-child(2) { white-space: nowrap; }
.mr .mr-members span { overflow-wrap: anywhere; }
.mr .mr-reason { max-width: 250px; overflow-wrap: anywhere; color: var(--mr-muted); font-size: 11px; }
.mr .mr-alias { border-top: 1px solid var(--mr-line); margin-top: 4px; padding-top: 3px; }
.mr .mr-lookup-group { padding: 4px 6px; }
.mr .mr-lookup-group h4 { display: flex; align-items: center; gap: 4px; }
.mr .mr-lookup-group li { display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: start; gap: 4px; padding: 3px 0; border-bottom: 1px solid var(--mr-line); }
.mr .mr-lookup-group code { color: var(--mr-muted); }

@container (max-width: 935px) {
  .mr .mr-columns { grid-template-columns: minmax(0,1fr); }
  .mr .mr-rail-toggle { display: inline-flex; }
  .mr .mr-sidebar:not([data-expanded=true]) .mr-rail-body { display: none; }
  .mr .mr-sidebar[data-expanded=true] .mr-rail-toggle .mr-icon { transform: rotate(180deg); }
  .mr .mr-scopes { max-height: 185px; }
}
@container (max-width: 715px) {
  .mr .mr-chart-body, .mr .mr-manager-grid:has(.mr-taxonomy-detail:not([hidden])) { grid-template-columns: minmax(0,1fr); }
  .mr .mr-manager-grid:has(.mr-taxonomy-detail:not([hidden])) .mr-taxonomy-detail { grid-row: 1; }
  .mr .mr-canvas { height: 300px; }
}
@container (max-width: 570px) {
  .mr .mr-task-chip { display: none; }
  .mr .mr-metrics { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 6px; }
  .mr .mr-secondary { grid-template-columns: minmax(0,1fr); }
  .mr .mr-matrix-top .mr-search { flex-basis: 100%; max-width: none; }
  .mr .mr-task-times { gap: 4px 10px; }
  .mr .mr-heading h1 { font-size: 17px; }
}
`;
