const W = 840;
const L = 66;
const R = 51;
const TOP = 17;
const BOTTOM = 30;
const plot = W - L - R;
const x = k => L + plot * (k + 1) / 240;
const labels = [
  [-1, '09:25'], [29, '10:00'], [59, '10:30'], [89, '11:00'], [119, '11:30 / 13:00'],
  [149, '13:30'], [179, '14:00'], [209, '14:30'], [239, '15:00'],
];

export const finite = v => typeof v === 'number' && Number.isFinite(v);
const minute = k => Number.isInteger(k) && k >= 0 && k < 240;
const fmt = (v, digits = 1) => finite(v) ? (v / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—';
const scale = (h, max) => v => TOP + (h - TOP - BOTTOM) * (1 - v / max);
const signalColor = direction => direction === 'SURGE' ? 'var(--vp-up)' : 'var(--vp-down)';

function path(points, pick, y) {
  const commands = [];
  let last = -2;
  for (const point of points) {
    const v = pick(point);
    if (!finite(v)) {
      last = -2;
      continue;
    }
    commands.push(`${point.k === last + 1 ? 'L' : 'M'}${x(point.k).toFixed(2)},${y(v).toFixed(2)}`);
    last = point.k;
  }
  return commands.join(' ');
}

function nice(value) {
  const base = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(2 * value / base) * base / 2;
}

function grid(max, height, y, rightLabels) {
  let svg = '';
  for (let i = 0; i <= 4; i += 1) {
    const value = max * i / 4;
    const yy = y(value);
    svg += `<line class="grid" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/>
      <text x="${L - 8}" y="${yy + 3}" text-anchor="end">${fmt(value)}</text>`;
    if (rightLabels) svg += `<text x="${W - R + 7}" y="${yy + 3}">${rightLabels(i / 4)}</text>`;
  }
  for (const [k, label] of labels) {
    const anchor = k === -1 ? 'start' : k === 239 ? 'end' : 'middle';
    svg += `<line class="grid" x1="${x(k)}" x2="${x(k)}" y1="${TOP}" y2="${height - BOTTOM}"/>
      <text x="${x(k)}" y="${height - 9}" text-anchor="${anchor}">${label}</text>`;
  }
  return svg + `<text x="${L - 8}" y="10" text-anchor="end">亿元</text>`;
}

function differenceArea(points, y) {
  const paths = { up: [], down: [] };
  const add = (a, b, ta, tb, pa, pb, diff) => {
    paths[diff >= 0 ? 'up' : 'down'].push(`M${a} ${y(ta)} L${b} ${y(tb)} L${b} ${y(pb)} L${a} ${y(pa)}Z`);
  };
  for (let i = 1; i < points.length; i += 1) {
    const p = points[i - 1];
    const q = points[i];
    if (q.k !== p.k + 1 || ![p.today, p.previous, q.today, q.previous].every(finite)) continue;
    const da = p.today - p.previous;
    const db = q.today - q.previous;
    if (da * db < 0) {
      const fraction = da / (da - db);
      const middle = x(p.k) + (x(q.k) - x(p.k)) * fraction;
      const crossing = p.today + (q.today - p.today) * fraction;
      add(x(p.k), middle, p.today, crossing, p.previous, crossing, da);
      add(middle, x(q.k), crossing, q.today, crossing, q.previous, db);
    } else {
      add(x(p.k), x(q.k), p.today, q.today, p.previous, q.previous, da || db);
    }
  }
  return Object.entries(paths).map(([tone, d]) => `<path d="${d.join(' ')}" fill="var(--vp-${tone})" opacity=".15"/>`).join('');
}

function dot(k, value, y, color, radius, attributes = '') {
  return finite(value) ? `<circle cx="${x(k)}" cy="${y(value)}" r="${radius}" fill="${color}" ${attributes}/>` : '';
}

function box(kind, label, height, content) {
  return `<svg class="vp-chart" data-chart="${kind}" viewBox="0 0 ${W} ${height}" role="img" aria-label="${label}">
    ${content}<line class="cross" visibility="hidden" x1="0" x2="0" y1="${TOP}" y2="${height - BOTTOM}"/>
  </svg>`;
}

function snapshotX(snapshot) {
  return snapshot.k - 1 + snapshot.elapsed / 60;
}

export function charts(rawPoints, showPrice = true, options = {}) {
  const points = (rawPoints ?? []).filter(p => p && minute(p.k));
  const { auction, events = [] } = options;
  const candidate = options.snapshot;
  const snapshot = candidate && minute(candidate.k) && finite(candidate.elapsed) && candidate.elapsed >= 0 &&
    candidate.elapsed <= 60 && finite(candidate.today) ? candidate : null;
  const amounts = [...points.flatMap(p => [p.today, p.previous]), auction?.today, auction?.previous, snapshot?.today].filter(finite);
  if (!amounts.length) return [
    '<div class="vp-plot-empty">等待有效分时</div>', '<div class="vp-plot-empty">等待完整的边际窗口</div>',
  ];
  const high = 269;
  const low = 218;
  const max = nice(Math.max(1, ...amounts));
  const maxDelta = nice(Math.max(1, ...points.flatMap(p => [p.today_delta, p.previous_delta]).filter(finite)));
  const maxRatio = Math.max(2, Math.ceil(Math.max(1, ...[...points.map(p => p.multiple), snapshot?.multiple].filter(finite)) * 2) / 2);
  const y = scale(high, max);
  const yd = scale(low, maxDelta);
  const yr = scale(low, maxRatio);

  const prices = showPrice ? [...points.map(p => p.price), snapshot?.price].filter(finite) : [];
  const floor = prices.length ? Math.floor(Math.min(...prices)) : null;
  const spread = prices.length ? Math.max(1, Math.ceil(Math.max(...prices)) - floor) : null;
  const pricePath = prices.length ? `<path d="${path(points, p => p.price, v => scale(high, spread)(v - floor))}"
     class="vp-price-line"/>` : '';
  const priceLabels = prices.length ? f => (floor + spread * f).toFixed(0) : null;

  const auctionDots = dot(-1, auction?.previous, y, 'var(--vp-muted)', 2.8) +
    dot(-1, auction?.today, y, 'var(--vp-up)', 3.7);
  const eventDots = events.map(event => {
    if (!minute(event.k)) return '';
    const p = points[event.k]?.k === event.k ? points[event.k] : points.find(p => p.k === event.k);
    const usingSnapshot = snapshot?.k === event.k && !finite(p?.today);
    const value = usingSnapshot ? snapshot.today : p?.today;
    return dot(usingSnapshot ? snapshotX(snapshot) : event.k, value, y, signalColor(event.direction), 4.4,
      'stroke="var(--vp-card)" stroke-width="1.5"');
  }).join('');
  let liveMark = '';
  let liveRatio = '';
  if (snapshot) {
    const liveX = snapshotX(snapshot);
    const anchor = points.find(p => p.k + 1 === snapshot.k);
    if (finite(anchor?.today)) {
      liveMark = `<path class="vp-live-tail" d="M${x(anchor.k)} ${y(anchor.today)} L${x(liveX)} ${y(snapshot.today)}"/>`;
    }
    liveMark += dot(liveX, snapshot.today, y, 'var(--vp-up)', 4.2, 'class="vp-snapshot-dot"');
    liveRatio = dot(liveX, snapshot.multiple, yr, 'var(--vp-amber)', 3.3, 'class="vp-snapshot-dot"');
  }
  const upper = box('cum', '今日与上一交易日累计成交额', high, `
    ${grid(max, high, y, priceLabels)}${differenceArea(points, y)}
    <path class="vp-day-line prev" d="${path(points, p => p.previous, y)}"/>
    <path class="vp-day-line" d="${path(points, p => p.today, y)}"/>
    ${pricePath}${auctionDots}${eventDots}${liveMark}`);

  let bars = '';
  for (const p of points) {
    if (finite(p.previous_delta)) {
      bars += `<rect x="${x(p.k) - 1.45}" y="${yd(p.previous_delta)}" width="2.9"
        height="${yd(0) - yd(p.previous_delta)}" fill="var(--vp-muted)" opacity=".30"/>`;
    }
    if (finite(p.today_delta)) {
      const tone = finite(p.previous_delta) && p.today_delta < p.previous_delta ? 'down' : 'up';
      bars += `<rect x="${x(p.k) - .9}" y="${yd(p.today_delta)}" width="1.8"
        height="${yd(0) - yd(p.today_delta)}" fill="var(--vp-${tone})" opacity=".8"/>`;
    }
  }
  const lower = box('delta', '单分钟成交额与边际量比', low, `
    ${grid(maxDelta, low, yd, f => (f * maxRatio).toFixed(1) + '×')}${bars}
    <line class="vp-ratio-ref" x1="${L}" x2="${W - R}" y1="${yr(1)}" y2="${yr(1)}"/>
    <path class="vp-ratio-line" d="${path(points, p => p.multiple, yr)}"/>${liveRatio}`);
  return [upper, lower];
}

export function bindHover(root, row, onChange) {
  const svgs = [...root.querySelectorAll('svg[data-chart]')];
  const tip = root.querySelector('.vp-hover');
  const wrap = root.querySelector('.vp-chart-wrap');
  const points = new Map(row.points.map(p => [p.k, p]));
  const auction = { time: '09:25 集合竞价终态', ...row.auction };
  const snap = row.snapshot;
  const ratio = v => finite(v) ? v.toFixed(2) + '×' : '—';
  function select(key, svg = svgs[0]) {
    if (!svg) return;
    const p = key === -1 ? auction : key === 'snapshot' ? snap : points.get(key);
    tip.hidden = !p;
    for (const s of svgs) {
      const line = s.querySelector('.cross');
      line.setAttribute('visibility', p ? 'visible' : 'hidden');
      if (p) {
        const at = x(key === 'snapshot' ? snapshotX(snap) : key);
        line.setAttribute('x1', at);
        line.setAttribute('x2', at);
      }
    }
    if (!p) return;
    const pos = x(key === 'snapshot' ? snapshotX(snap) : key) / W;
    const label = key === -1 ? auction.time : key === 'snapshot' ? `${p.stamp.slice(11, 19)} 未闭合快照` : `${p.time} 闭合分钟`;
    const values = `${label}\n今日 ${fmt(p.today)} 亿 · 上一交易日 ${fmt(p.previous)} 亿`;
    tip.textContent = key === -1 ? values : `${values}\n分钟 ${fmt(p.today_delta)} 亿 · 边际量比 ${ratio(p.multiple)}`;
    const offset = svg.getBoundingClientRect().left - wrap.getBoundingClientRect().left;
    tip.style.left = `${offset + Math.max(2, Math.min(pos * svg.clientWidth + 9, svg.clientWidth - tip.offsetWidth - 5))}px`;
  }
  for (const svg of svgs) {
    svg.onpointermove = event => {
      const rect = svg.getBoundingClientRect();
      const u = (event.clientX - rect.left) / rect.width * W;
      const k = Math.round((u - L) / plot * 240) - 1;
      const key = snap && Math.abs(u - x(snapshotX(snap))) < 4 ? 'snapshot' : (k >= -1 && k < 240 ? k : null);
      onChange(key);
      select(key, svg);
    };
    svg.onpointerleave = () => {
      onChange(null);
      select(null);
    };
  }
  return select;
}
