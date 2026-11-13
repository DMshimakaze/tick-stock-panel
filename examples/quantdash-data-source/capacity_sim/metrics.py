"""Weighted per-security/time freshness; unavailable coverage stays unavailable."""

from collections import defaultdict
import math
from bisect import bisect_right

from .model import at, latest_closed, session_points


def quantiles(values):
    """(seconds, weight); missing observations use +inf, serialized as null."""
    ordered = sorted(values)
    count = sum(weight for _, weight in ordered)
    if not count:
        return dict(p50=None, p95=None, max=None, observations=0, unavailable=0)

    def pick(fraction):
        accumulated = 0
        for value, weight in ordered:
            accumulated += weight
            if accumulated >= count * fraction:
                return round(value, 3) if math.isfinite(value) else None

    finite = [(v, w) for v, w in ordered if math.isfinite(v)]
    result = dict(p50=pick(0.5), p95=pick(0.95), max=pick(1), observations=count,
                  unavailable=sum(w for v, w in ordered if not math.isfinite(v)))
    if finite:
        # Covered-only p95 is supplemental, never used to pass an SLO.
        total, acc = sum(w for _, w in finite), 0
        for value, weight in finite:
            acc += weight
            if acc >= total * 0.95:
                result["covered_p95"] = round(value, 3)
                break
        result["covered_max"] = round(finite[-1][0], 3)
    return result


def _freshness(records, samples, weights, *, quotes=False, wall_clock=False):
    by_batch = defaultdict(list)
    for done, target, batch, _ in records:
        by_batch[batch].append((done, target))
    values = []
    for batch, weight in weights.items():
        events, cursor, latest = sorted(by_batch[batch]), 0, None
        for sample in samples:
            while cursor < len(events) and events[cursor][0] <= sample:
                latest = max(latest if latest is not None else -1e9, events[cursor][1])
                cursor += 1
            reference = sample if quotes else latest_closed(sample)
            if latest is None:
                age = math.inf
            elif quotes or wall_clock:
                age = max(0, reference - latest)
            else:
                # Lunch has no expected new minutes. Also report wall-clock age
                # separately so a retained 11:30 row is never relabeled 13:00.
                points = session_points(1)
                age = max(0, bisect_right(points, reference) - bisect_right(points, latest)) * 60
            values.append((age, weight))
    return quantiles(values)


def summarize(sim):
    cfg = sim.cfg
    weights = {i: min(100, cfg.universe - i * 100) for i in range(math.ceil(cfg.universe / 100))}
    samples = [p + offset for p in session_points(1, day=cfg.day, holiday=cfg.holiday)
               for offset in (0, 15, 30, 45) if p + offset <= cfg.stop_s
               and p + offset <= (at("11:30") if p <= at("11:30") else at("15:00"))]
    ma_records = defaultdict(list)
    for done, target, batch, size in sim.observations["ma55"]:
        if cfg.warmup_minutes:
            done = max(done, sim.history_ready.get(batch, math.inf))
        ma_records[batch].append((target, done))
    def bar_delays(period):
        values = []
        for closed in session_points(period, day=cfg.day, holiday=cfg.holiday):
            if closed + 60 > cfg.stop_s:
                continue
            for batch, size in weights.items():
                available = min((done for target, done in ma_records[batch] if target >= closed), default=math.inf)
                values.append((available + cfg.consumer_s - closed, size))
        return quantiles(values)
    quote_weights = {0: cfg.universe, 1: cfg.etfs, 2: 4}
    quote_age = _freshness(sim.observations["quotes"], samples, quote_weights, quotes=True)
    full_age = _freshness(sim.observations["full_1m"], samples, {-1: cfg.universe})
    ma_age = bar_delays(15)
    focus_delays = [(done - target, size) for done, target, _, size in sim.observations["focus"]]
    depth_delays = [(done - target, size) for done, target, _, size in sim.observations["depth"]]
    rounds = {key: quantiles([(value, 1) for value in values])
              for key, values in sorted(sim.round_durations.items())}
    pipeline = dict(sim.pipeline, finished_stages=sim.finished_stages)
    if not sim.stage_remaining:
        pipeline["status"] = "NOT_SCHEDULED"
    if pipeline["finished_s"] is not None:
        pipeline["finished_s"] = round(pipeline["finished_s"], 3)
    updates = {batch: len(ma_records[batch]) for batch in weights}
    minima, maxima = min(updates.values()), max(updates.values())

    def within(stat, bound):
        return stat["p95"] is not None and stat["p95"] <= bound

    attention = (sim.counts["local_skipped"] == 0 and sim.counts["failed_focus"] == 0
                 and sim.counts["failed_depth"] == 0 and
                 (not cfg.focus or bool(focus_delays)) and
                 (not cfg.depth or bool(depth_delays)) and
                 all(d <= 120 for d, _ in focus_delays + depth_delays))
    slo = dict(snapshot_60s=within(quote_age, 60), ma55_5min=within(ma_age, 300) and ma_age["unavailable"] == 0,
               ma55_3min=within(ma_age, 180) and ma_age["unavailable"] == 0, full_1m_5min=within(full_age, 300),
               pipeline_1730=pipeline["status"] == "PASS_CAPACITY_ONLY",
               attention_120s=attention,
               no_vendor_429=sim.counts["rejected_429"] == 0)
    count_keys = ("offered", "sent", "completed", "pending", "failed", "aborted",
                  "deadline_missed", "local_skipped", "rejected_429", "network_timeouts", "superseded")
    recovery, recovery_target = None, None
    if cfg.fault == "outage":
        end = at("10:00") + cfg.outage_minutes * 60
        recovery_target = latest_closed(end - 60, cfg.ma_period)
        recovered = [min((done for target, done in ma_records[batch]
                          if target >= recovery_target and done >= end), default=math.inf)
                     for batch in weights]
        if all(math.isfinite(t) for t in recovered):
            recovery = round(max(recovered) + cfg.consumer_s - end, 3)
    result = dict(
        parameters=sim.parameters(), requests={k: sim.counts[k] for k in count_keys},
        peaks_60s=dict(vendor_attempts={k: q.peak for k, q in sorted(sim.attempts.items())},
                       local={k: q.peak for k, q in sorted(sim.local.items())},
                       coordinator={k: q.peak for k, q in sorted(sim.broker.items())}),
        round_completion_s=rounds,
        freshness_s=dict(snapshot=quote_age, ma55_bar=ma_age, full_1m=full_age,
                         full_1m_wall_clock=_freshness(sim.observations["full_1m"], samples, {-1: cfg.universe}, wall_clock=True),
                         chart_30m=bar_delays(30), chart_60m=bar_delays(60),
                         focus=quantiles(focus_delays), depth=quantiles(depth_delays),
                         snapshot_by_asset={name: _freshness(sim.observations["quotes"], samples,
                            {batch: quote_weights[batch]}, quotes=True)
                            for batch, name in enumerate(("stock", "etf", "index"))}),
        host_repair=dict(publication="ATOMIC_ROUND_FIXED_QUERY_END", max_concurrent_rounds=1,
                         missed_cadence_slots=sim.missed_slots, first_missed_cadence_s=sim.first_missed_cadence),
        pipeline=pipeline, slo=slo, queue_peak=sim.queue_peak,
        first_deadline_miss_s=sim.first_overdue, last_http_s=sim.last_http,
        retries=dict(count=len(sim.retry_delays), min_retry_after_s=min(sim.retry_delays, default=None)),
        recovery_s=recovery, recovery_target_s=recovery_target,
        estimated_volume=dict(wire_MB=round(sim.counts["wire_bytes"] / 1e6, 3),
                              cache_write_MB=round(sim.counts["estimated_write_bytes"] / 1e6, 3),
                              decoded_rows=sim.counts["decoded_rows"],
                              upsert_bar_rows=sim.counts["upsert_bar_rows"],
                              retained_bars_MB=round(cfg.universe * (144 + 6 * 256) * cfg.disk_row_bytes / 1e6, 3)),
        fairness=dict(min_live_updates=minima, max_live_updates=maxima,
                      least_over_most=round(minima / maxima, 4) if maxima else 0,
                      priority_batch_updates=updates[0], missing_batches=sum(v == 0 for v in updates.values())),
        available_dates=dict(minute_signals=cfg.day if sim.observations["focus"] else None,
                             full_1m_rotation=cfg.day if sim.observations["full_1m"] else None,
                             classification="UNSUPPORTED / SOURCE_MISSING"),
        evidence="SYNTHETIC_OFFLINE_PREDICTION", operational_status="NOT_VERIFIED",
    )
    return result
