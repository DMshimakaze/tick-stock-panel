/** Equivalent port of structure.advance / features.minimal_features at 2347762.
 * Input: validated completed adjusted-price 15m OHLCV; timestamps: Beijing ends.
 * Volume stays in the host's lots: every volume comparison is dimensionless.
 */
export const RULE_VERSION = 'MA55_15M_MINIMAL_V5';
export const RULES = Object.freeze({
  near_above: .010, near_below: .018, impulse_body: .008,
  impulse_volume: 1.5, impulse_memory: 48, active_range: .025,
  test_window: 4, full_departure: .015, deeper_foot: .005,
});

// Non-overlapping partials keep causal MA comparisons aligned with math.fsum.
function fsum(values) {
  const partials = [];
  for (let x of values) {
    let i = 0;
    for (let y of partials) {
      if (Math.abs(x) < Math.abs(y)) [x, y] = [y, x];
      const hi = x + y, lo = y - (hi - x);
      if (lo) partials[i++] = lo;
      x = hi;
    }
    partials.length = i;
    partials.push(x);
  }
  let n = partials.length, hi = 0, lo = 0;
  if (n) {
    hi = partials[--n];
    while (n) {
      const x = hi, y = partials[--n];
      hi = x + y;
      lo = y - (hi - x);
      if (lo) break;
    }
    if (n && ((lo < 0 && partials[n - 1] < 0) || (lo > 0 && partials[n - 1] > 0))) {
      const y = lo * 2, x = hi + y;
      if (y === x - hi) hi = x;
    }
  }
  return hi;
}
function median(values) {
  const v = [...values].sort((a, b) => a - b), n = v.length;
  return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

export function minimalFeatures(history, bar, slotVolumes) {
  const rows = [...history, bar];
  const known = history.length >= 55 ? fsum(history.slice(-55).map(r => r.close)) / 55 : NaN;
  const ma = rows.length >= 55 ? fsum(rows.slice(-55).map(r => r.close)) / 55 : NaN;
  const baseline = history.length >= 20 ? median(history.slice(-20).map(r => r.volume)) : NaN;
  const slotBase = slotVolumes.length >= 3 ? median(slotVolumes.slice(-5)) : NaN;
  const tr = rows.map((r, i) => i ? Math.max(r.high - r.low,
    Math.abs(r.high - rows[i - 1].close), Math.abs(r.low - rows[i - 1].close)) : r.high - r.low);
  return { ...bar, known_ma55: known, ma55: ma,
    range16: rows.length >= 16 ? Math.max(...rows.slice(-16).map(r => r.high)) /
      Math.min(...rows.slice(-16).map(r => r.low)) - 1 : NaN,
    volume_baseline: baseline, vol20: baseline > 0 ? bar.volume / baseline : NaN,
    vol_slot: slotBase > 0 ? bar.volume / slotBase : NaN,
    slot_baseline: slotBase, atr20: tr.length >= 20 ? fsum(tr.slice(-20)) / 20 : NaN,
  };
}

export function advance(previous, bar, rawTick = .01, rules = RULES) {
  const s = previous ? structuredClone(previous) : { index: -1, last_impulse: null,
    pending: null, locked: false, last_buy: null, saw_downward_retest: false,
    previous_at: null, previous_close: null };
  const i = s.index + 1, stamp = String(bar.timestamp);
  const level = bar.known_ma55, ma = bar.ma55;
  const ready = Number.isFinite(level) && level > 0, tick = rawTick * bar.adjust_factor;
  const red = bar.close - bar.open >= 2 * tick;
  const volumePush = ['vol20', 'vol_slot'].some(k => Number.isFinite(bar[k]) && bar[k] >= rules.impulse_volume);
  const body = bar.close / bar.open - 1;
  const impulse = red && volumePush && (body >= rules.impulse_body ||
    (body >= .003 && bar.range16 >= rules.active_range));
  if (impulse) s.last_impulse = { index: i, at: stamp, volume: bar.volume,
    ratio20: bar.vol20, ratio_slot: bar.vol_slot };
  const active = s.last_impulse !== null && i - s.last_impulse.index <= rules.impulse_memory;
  const contact = ready && level * (1 - rules.near_below) <= bar.low &&
    bar.low <= level * (1 + rules.near_above) && bar.high >= level * (1 - rules.near_above);
  const failed = ready && bar.close < ma * (1 - rules.near_above);
  const departed = ready && bar.low > level * (1 + rules.full_departure) && bar.close > ma * (1 + rules.full_departure);
  const buy = s.last_buy;
  const deeper = s.locked && ready && buy !== null && i - buy.index >= 2 && contact &&
    s.previous_close !== null && bar.low < buy.low - rules.deeper_foot * level && bar.close < s.previous_close;
  let event = '', veto = '', test = null;
  if (s.previous_at && stamp.slice(0, 10) !== s.previous_at.slice(0, 10)) s.pending = null;
  if (s.locked && departed) Object.assign(s, { locked: false, pending: null, saw_downward_retest: false });
  const fresh = s.locked && impulse && contact && buy !== null && i - buy.index >= 5 && s.saw_downward_retest;
  if (fresh || deeper) Object.assign(s, { locked: false, saw_downward_retest: false,
    pending: { index: i, at: stamp, low: bar.low } });
  if (s.locked && contact && bar.close < bar.open) s.saw_downward_retest = true;
  if (!ready) veto = 'WARMUP';
  else if (failed) {
    Object.assign(s, { locked: false, saw_downward_retest: false, pending: null });
    veto = 'CLOSE_BELOW_SUPPORT';
  } else if (s.locked) veto = 'SAME_SUPPORT_SWING';
  else {
    if (s.pending !== null && i - s.pending.index > rules.test_window) s.pending = null;
    const priorLow = s.pending ? s.pending.low : NaN;
    if (contact) {
      if (s.pending === null) s.pending = { index: i, at: stamp, low: bar.low };
      else s.pending.low = Math.min(s.pending.low, bar.low);
    }
    test = s.pending ? structuredClone(s.pending) : null;
    if (test === null) veto = 'AWAIT_SUPPORT_TEST';
    else if (deeper) veto = 'NEW_LOWER_FOOT';
    else if (red && bar.close >= ma && active && (impulse || bar.range16 >= rules.active_range) &&
      (impulse || i - test.index >= 2 || (Number.isFinite(priorLow) && bar.low < priorLow - tick))) {
      event = 'BUY_STRUCTURE';
      Object.assign(s, { locked: true, last_buy: { index: i, at: stamp, low: test.low },
        saw_downward_retest: false, pending: null });
    } else if (!active) veto = 'NO_REAL_ADVANCE';
    else veto = 'AWAIT_FIRST_RED';
  }
  Object.assign(s, { index: i, previous_at: stamp, previous_close: bar.close });
  const decision = { timestamp: stamp, event, veto,
    pattern: event ? (test.at === stamp ? 'SINGLE_BAR' : 'MULTI_BAR') : '',
    support_test_time: test?.at ?? null, support_test_low: test?.low ?? null,
    known_ma55: level, ma55: ma, volume_ratio20: bar.vol20, volume_ratio_same_clock: bar.vol_slot,
    impulse_now: impulse, impulse_recent: active, recent_range_pct: 100 * bar.range16,
    contact, close_distance_pct: ready ? 100 * (bar.close / ma - 1) : null };
  return { structure: s, decision };
}
