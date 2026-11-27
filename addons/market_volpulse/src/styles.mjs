export const CSS = `
.vp {
  --vp-bg:hsl(var(--surface,220 18% 12%));
  --vp-card:hsl(var(--elevated,220 17% 16%));
  --vp-text:hsl(var(--fg-primary,215 30% 94%));
  --vp-muted:hsl(var(--fg-secondary,215 15% 65%));
  --vp-border:hsl(var(--border,220 15% 25%));
  --vp-up:#ef5350;
  --vp-down:#20aa87;
  --vp-blue:#63a5f5;
  --vp-amber:#e0a75b;
  --vp-mono:ui-monospace,Consolas,'Cascadia Code',monospace;
  color:var(--vp-text);
  font:13px/1.5 system-ui,'Microsoft YaHei',sans-serif;
  max-width:1580px;
  margin:auto;
  padding:20px 24px 28px;
  box-sizing:border-box;
  min-width:0;
}
.vp * {
  box-sizing:border-box;
}
.vp h1,.vp h2,.vp p {
  margin:0;
}
.vp h1 {
  font-size:26px;
  letter-spacing:-.5px;
  font-weight:680;
}
.vp h2 {
  font-size:14px;
  font-weight:650;
}
.vp button {
  font:inherit;
  color:inherit;
  cursor:pointer;
  background:var(--vp-bg);
  border:1px solid var(--vp-border);
  border-radius:7px;
  padding:7px 12px;
}
.vp button:hover {
  border-color:var(--vp-blue);
}
.vp button[aria-pressed=true] {
  color:var(--vp-blue);
  border-color:var(--vp-blue);
  background:color-mix(in srgb,var(--vp-blue) 8%,var(--vp-card));
}
.vp button:focus-visible {
  outline:2px solid var(--vp-blue);
  outline-offset:2px;
}
.vp-head,.vp-section-head,.vp-status,.vp-tools,.vp-legend {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  flex-wrap:wrap;
}
.vp-sub,.vp-muted {
  color:var(--vp-muted);
}
.vp-sub {
  font-size:12px;
  margin-top:3px!important;
}
.vp-status {
  justify-content:flex-end;
  font:11px var(--vp-mono);
  color:var(--vp-muted);
}
.vp-dot {
  display:inline-block;
  width:6px;
  height:6px;
  border-radius:50%;
  background:var(--vp-muted);
  margin-right:6px;
}
.vp-dot.live {
  background:var(--vp-down);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--vp-down) 16%,transparent);
}
.vp-badge {
  padding:5px 8px;
  border:1px solid var(--vp-border);
  border-radius:5px;
}
.vp-hero {
  display:grid;
  grid-template-columns:1.4fr 1fr 1fr 1fr;
  gap:10px;
  margin:19px 0 11px;
}
.vp-card,.vp-panel {
  background:var(--vp-card);
  border:1px solid var(--vp-border);
  border-radius:10px;
  min-width:0;
}
.vp-card {
  padding:16px 17px;
}
.vp-card.hero-main {
  background:linear-gradient(115deg,color-mix(in srgb,var(--vp-blue) 6%,var(--vp-card)),var(--vp-card) 75%);
  border-top:2px solid var(--vp-blue);
}
.vp-label {
  font-size:11px;
  color:var(--vp-muted);
  font-weight:600;
  letter-spacing:.3px;
}
.vp-number {
  font-family:var(--vp-mono);
  font-size:clamp(20px,2.35vw,32px);
  font-weight:700;
  line-height:1.35;
  letter-spacing:-1.4px;
  margin:7px 0 4px;
  white-space:nowrap;
  font-variant-numeric:tabular-nums;
}
.vp-unit {
  font-size:12px;
  font-weight:500;
  letter-spacing:0;
  margin-left:4px;
  color:var(--vp-muted);
}
.vp-small {
  font-size:11px;
  color:var(--vp-muted);
  white-space:normal;
  line-height:1.55;
}
.vp-up {
  color:var(--vp-up)!important;
}
.vp-down {
  color:var(--vp-down)!important;
}
.vp-index-grid {
  display:grid;
  grid-template-columns:repeat(5,minmax(0,1fr));
  gap:8px;
  margin-bottom:16px;
}
.vp-index {
  background:var(--vp-card)!important;
  text-align:left;
  border-radius:8px!important;
  padding:11px 13px!important;
  min-width:0;
}
.vp-index[aria-pressed=true] {
  box-shadow:inset 0 -2px var(--vp-blue);
}
.vp-index-top,.vp-index-bottom {
  display:flex;
  justify-content:space-between;
  align-items:baseline;
  gap:6px;
  flex-wrap:wrap;
}
.vp-index-name {
  font-weight:650;
  color:var(--vp-text);
  white-space:nowrap;
}
.vp-index-bottom {
  font-family:var(--vp-mono);
  font-size:11px;
  margin-top:6px;
  color:var(--vp-muted);
}
.vp-index-num {
  font-family:var(--vp-mono);
  font-weight:650;
  white-space:nowrap;
}
.vp-layout {
  display:grid;
  grid-template-columns:minmax(0,1fr) 278px;
  gap:12px;
  align-items:start;
}
.vp-panel {
  padding:15px 16px;
}
.vp-section-head {
  margin-bottom:10px;
}
.vp-section-head>div:first-child {
  min-width:0;
}
.vp-chart-title {
  margin-bottom:2px!important;
}
.vp-chart {
  width:100%;
  min-height:0;
  display:block;
  overflow:visible;
}
.vp-chart text {
  font-family:var(--vp-mono);
  font-size:10px;
  fill:var(--vp-muted);
}
.vp-chart .grid {
  stroke:var(--vp-border);
  stroke-width:.8;
  opacity:.65;
}
.vp-chart .cross {
  stroke:var(--vp-muted);
  stroke-width:1;
  stroke-dasharray:3 4;
  pointer-events:none;
}
.vp-legend {
  justify-content:flex-start;
  color:var(--vp-muted);
  font-size:11px;
  margin:0 0 9px;
}
.vp-key {
  display:inline-block;
  width:14px;
  height:2px;
  background:currentColor;
  vertical-align:middle;
  margin-right:5px;
}
.vp-key.dash {
  border-top:2px dashed currentColor;
  background:none;
}
.vp-divider {
  height:1px;
  background:var(--vp-border);
  margin:9px 0 14px;
}
.vp-chart-wrap {
  position:relative;
}
.vp-hover {
  position:absolute;
  left:55px;
  top:0;
  z-index:1;
  pointer-events:none;
  background:var(--vp-bg);
  border:1px solid var(--vp-border);
  border-radius:6px;
  padding:7px 10px;
  color:var(--vp-text);
  font:11px/1.6 var(--vp-mono);
  white-space:pre-line;
  box-shadow:0 4px 20px #0003;
}
.vp-event-list {
  display:flex;
  flex-direction:column;
  gap:0;
  margin-top:8px;
  max-height:410px;
  overflow:auto;
}
.vp-event {
  border-bottom:1px solid var(--vp-border);
  padding:11px 0;
  display:grid;
  grid-template-columns:1fr auto;
  gap:3px;
}
.vp-event:last-child {
  border-bottom:0;
}
.vp-event-time {
  font:11px var(--vp-mono);
  color:var(--vp-muted);
}
.vp-event-metrics {
  font:11px var(--vp-mono);
  color:var(--vp-muted);
  grid-column:1/-1;
}
.vp-rule {
  margin-top:12px;
}
.vp-rule-row {
  display:flex;
  justify-content:space-between;
  gap:8px;
  padding:8px 0;
  border-bottom:1px solid var(--vp-border);
  font-size:11px;
}
.vp-rule-row:last-child {
  border-bottom:0;
}
.vp-rule-row strong {
  font:600 11px var(--vp-mono);
}
.vp-empty {
  padding:20px 2px;
  text-align:center;
  color:var(--vp-muted);
  font-size:12px;
}
.vp-notice {
  background:color-mix(in srgb,var(--vp-amber) 8%,var(--vp-card));
  color:var(--vp-amber);
  border:1px solid color-mix(in srgb,var(--vp-amber) 35%,var(--vp-border));
  padding:8px 11px;
  border-radius:7px;
  font-size:12px;
  margin:9px 0;
}
.vp-foot {
  display:flex;
  justify-content:space-between;
  gap:8px;
  flex-wrap:wrap;
  color:var(--vp-muted);
  font-size:10px;
  margin-top:12px;
}
.vp-plot-empty {
  height:200px;
  display:grid;
  place-content:center;
  color:var(--vp-muted);
}
@media(max-width:1120px) {
  .vp-layout {
    grid-template-columns:minmax(0,1fr) 235px;
  }
  .vp {
    padding:14px;
  }
  .vp-card {
    padding:12px;
  }
  .vp-number {
    font-size:23px;
  }
  .vp-index {
    padding:9px!important;
  }
}
@media(max-width:830px) {
  .vp-layout {
    grid-template-columns:1fr;
  }
  .vp-hero {
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
  .vp-index-grid {
    grid-template-columns:repeat(3,minmax(0,1fr));
  }
  .vp-index-num {
    font-size:11px;
  }
  .vp-layout>aside {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:10px;
  }
  .vp-rule {
    margin-top:0;
  }
}
@media(max-width:490px) {
  .vp {
    padding:9px;
  }
  .vp-index-grid {
    grid-template-columns:repeat(2,minmax(0,1fr));
  }
  .vp-layout>aside {
    display:block;
  }
  .vp-rule {
    margin-top:10px;
  }
  .vp-number {
    font-size:20px;
  }
  .vp-head {
    align-items:flex-start;
  }
  .vp-card {
    padding:10px;
  }
}

html:not(.dark) .vp {
  --vp-up: #b81a32;
  --vp-down: #08745b;
  --vp-blue: #215fb7;
  --vp-amber: #875206;
}

.vp { padding-block: 17px 25px; container-type: inline-size; }
.vp-status { gap: 8px; }
.vp-date select { min-width: 120px; }
.vp :is(select, input[type=number]) {
  font: inherit;
  color: var(--vp-text);
  background: var(--vp-bg);
  border: 1px solid var(--vp-border);
  border-radius: 7px;
  padding: 6px 8px;
}
.vp :is(button, select, input):disabled { opacity: .55; cursor: default; }
.vp :is(select, input):focus-visible { outline: 2px solid var(--vp-blue); outline-offset: 1px; }
.vp-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.vp-scope { text-align: right; }
.vp-chart { max-width: 1040px; margin-inline: auto; }
.vp-price-key { color: var(--vp-blue); }
.vp-ratio-key { color: var(--vp-amber); }
.vp-day-line { fill: none; stroke: var(--vp-up); stroke-width: 2.1; stroke-linejoin: round; }
.vp-day-line.prev { stroke: var(--vp-muted); stroke-width: 1.6; stroke-dasharray: 5 4; opacity: .85; }
.vp-price-line { fill: none; stroke: var(--vp-blue); stroke-width: 1.3; opacity: .9; }
.vp-live-tail { fill: none; stroke: var(--vp-up); stroke-width: 1.5; stroke-dasharray: 3 2; }
.vp-snapshot-dot { stroke: var(--vp-card); stroke-width: 1.5; }
.vp-ratio-line { fill: none; stroke: var(--vp-amber); stroke-width: 1.8; stroke-linejoin: round; }
.vp-ratio-ref { stroke: var(--vp-amber); stroke-dasharray: 4 4; opacity: .6; }
.vp button.vp-event { margin: 2px 0; border-radius: 6px; text-align: left; padding: 8px; background: var(--vp-card); }
.vp-event-list { max-height: 265px; }
.vp-rule-foot { margin-top: 10px !important; }
.vp-dialog {
  width: min(530px, calc(100vw - 24px));
  margin: auto;
  padding: 18px 20px;
  color: var(--vp-text);
  background: var(--vp-card);
  border: 1px solid var(--vp-border);
  border-radius: 12px;
  box-shadow: 0 20px 60px #0005;
}
.vp-dialog::backdrop { background: #0008; }
.vp-settings-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 13px 0;
}
.vp-settings-grid label { display: flex; flex-direction: column; gap: 4px; min-width: 0; color: var(--vp-muted); }
.vp-settings-grid .vp-switch { flex-direction: row; align-items: end; align-self: end; padding-block: 6px; }
.vp-settings-grid input[type=number] { width: 100%; }
.vp-dialog-actions { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 14px; }
.vp-dialog-actions output { color: var(--vp-amber); }
.vp-dialog-actions output[data-saved=true] { color: var(--vp-down); }
.vp-dialog-actions button { background: var(--vp-blue); color: var(--vp-bg); font-weight: 650; white-space: nowrap; }
.vp-toast {
  position: fixed;
  z-index: 20;
  inset: auto 18px 18px auto;
  padding: 10px 14px;
  border: 1px solid var(--vp-blue);
  border-radius: 8px;
  background: var(--vp-card);
  box-shadow: 0 4px 20px #0004;
}
@container (max-width: 820px) {
  .vp-layout { grid-template-columns: minmax(0, 1fr); }
  .vp-scope { text-align: left; }
}
@container (max-width: 560px) {
  .vp-hero { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 12px; }
  .vp-status { justify-content: flex-start; }
  .vp-chart text { font-size: 13px; }
}
`;
