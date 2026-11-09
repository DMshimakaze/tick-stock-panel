"""Source-backed workload shapes, comparison scenarios and an exhaustive grid."""

from dataclasses import dataclass, replace
import math

from .model import Config, Request, RoundSchedule, Simulator, at, session_points


@dataclass
class Workload:
    config: Config
    jobs: list[Request]
    schedules: list[RoundSchedule]
    full_minute: bool


def _batches(size, batch_size=100):
    return [(i, min(batch_size, size - i * batch_size)) for i in range(math.ceil(size / batch_size))]


def build(name: str, config: Config) -> Workload:
    cfg, full = config, name != "S0" and name != "S4"
    if name == "S0":
        cfg = replace(cfg, ma_period=5, focus=0, depth=0, fault="factor_unverified")
    elif name == "S2":
        cfg = replace(cfg, ma_period=15)
    elif name in ("S3-static", "S3-broker", "S4", "S5", "S6"):
        cfg = replace(cfg, host_rpm=30, policy="static" if name == "S3-static" else "broker")
        if name == "S4":
            cfg = replace(cfg, ma_period=15)
        if name == "S5":
            cfg = replace(cfg, fault=cfg.fault or "factor_unverified")
        if name == "S6":
            minutes = cfg.warmup_minutes or 60
            cfg = replace(cfg, warmup_minutes=minutes,
                          history_rows_s=cfg.universe * 3072 / (minutes * 60))
    elif name not in ("S1", "CUSTOM"):
        raise ValueError(f"unknown scenario: {name}")
    if not session_points(1, day=cfg.day, holiday=cfg.holiday):
        return Workload(cfg, [], [], full)
    jobs, schedules = [], []
    batches = _batches(cfg.universe)
    jobs.append(Request(at("09:25"), "marketdata", "quote_pool", "source_snapshot", 0,
                        cfg.universe, priority=2, lane="marketdata"))
    for i, size in _batches(cfg.metadata_symbols):
        jobs.append(Request(at("09:25"), "marketdata", "metadata", "metadata", i, size,
                            priority=2, lane="marketdata"))
    jobs.append(Request(at("09:25"), "marketdata", "daily", "calendar", 0, 1,
                        period="1d", bars=250, priority=2, lane="marketdata"))
    for point_index, point in enumerate(session_points(cfg.ma_period)):
        for i, size in batches:
            if point_index == 0:
                jobs.append(Request(point + 60, "marketdata", "factors", "source_factors", i,
                                    size, bars=5, priority=1, lane="marketdata"))
            jobs.append(Request(point + 60, "marketdata", "minute_batch", "ma55", i, size,
                                period=f"{cfg.ma_period}m", target_s=point, priority=1,
                                lane="marketdata", round_id=f"ma55:{point}",
                                deadline_s=point + 300))
    quotes = tuple(Request(0, "host", "quote_pool" if i < 2 else "quote_symbols",
                           "quotes", i, size, priority=0, lane="quotes")
                   for i, size in enumerate((cfg.universe, cfg.etfs, 4)))
    schedules.append(RoundSchedule("quotes", quotes, cfg.quote_seconds, at("09:15"),
                                   finish_relative=True, quotes=True))
    if full:
        templates = tuple(Request(0, "host", "minute_batch", "full_1m", i, size,
                                  period="1m", priority=3, lane="full_1m", deadline_s=300)
                          for i, size in batches)
        schedules.append(RoundSchedule("full_1m", templates, cfg.full_interval_minutes * 60,
                                       at("09:31")))
    if cfg.focus:
        templates = tuple(Request(0, "host", "minute_batch", "focus", i, size, period="1m",
                                  priority=1, lane="focus", deadline_s=120)
                          for i, size in _batches(cfg.focus))
        schedules.append(RoundSchedule("focus", templates, cfg.focus_seconds, at("09:31")))
    if cfg.depth:
        templates = tuple(Request(0, "host", "depth", "depth", i, 1, priority=1,
                                  lane="depth", nonblocking=cfg.policy == "independent",
                                  deadline_s=120) for i in range(cfg.depth))
        schedules.append(RoundSchedule("depth", templates, cfg.depth_seconds, at("09:31")))
    # Official pipeline order is serial; a missing factor fails before enriched.
    stages = [("daily", "daily", cfg.universe, "1d", 1),
              ("factors", "factors", cfg.universe, "", 5),
              ("enriched", "compute", 1, "", 1),
              ("index", "daily", 4, "1d", 1),
              ("etf_factors", "factors", cfg.etfs, "", 5),
              ("etf_daily", "daily", cfg.etfs, "1d", 1)]
    if full:
        stages.append(("minute", "minute_batch", cfg.universe, "1m", cfg.post_minute_bars))
    for stage, endpoint, size, period, bars in stages:
        # Official on_segment persistence splits custom minutes into 50 symbols;
        # Provider cannot combine future host calls into 100-symbol HTTP batches.
        for i, count in _batches(size, 50 if stage == "minute" else 100):
            jobs.append(Request(at("15:35"), "host", endpoint, "pipeline", i, count,
                                period=period, target_s=at("15:00"), bars=bars,
                                priority=2, lane="pipeline", round_id="pipeline", stage=stage))
    if cfg.warmup_minutes:
        # Same stock universe: 16-session COLD first, 64-session WARM deferred to
        # 15:20. The 60/120min values impose CPU work, not measured warmup times.
        for stage, when, days in (("COLD", 0, 16), ("WARM", at("15:20"), 64)):
            for i, size in batches:
                jobs.append(Request(when, "marketdata", "minute_batch", stage, i, size,
                                    period=f"{cfg.ma_period}m", target_s=at("15:00"),
                                    bars=days * (240 // cfg.ma_period), priority=5 if stage == "COLD" else 6,
                                    lane="marketdata", round_id=stage))
    return Workload(cfg, jobs, schedules, full)


def replay(name: str, config: Config | None = None):
    work = build(name, config or Config())
    sim = Simulator(work.config)
    sim.add(work.jobs)
    for schedule in work.schedules:
        sim.add_schedule(schedule)
    result = sim.run()
    result.update(scenario=name, full_minute=work.full_minute,
                  native15_equivalence="NOT_VERIFIED" if work.config.ma_period == 15 else "EXISTING_5M_INPUT",
                  data_coverage_assumption="COMPLETE_SYMBOLS_AND_FACTORS_EXCEPT_EXPLICIT_FAULTS")
    return result


def lower_bounds():
    """Ideal request-count bounds; exclude service time, gaps and retries."""
    return [dict(universe=size, batches=math.ceil(size / 100),
                 minutes_at_30_rpm=math.ceil(size / 100) / 30,
                 minutes_at_12_rpm=math.ceil(size / 100) / 12,
                 minutes_at_6_rpm=math.ceil(size / 100) / 6,
                 rpm_every_5m=math.ceil(size / 100) / 5,
                 rpm_every_15m=math.ceil(size / 100) / 15)
            for size in (5500, 5891, 6100)]


def search(config: Config, *, intervals=(1, 2, 3, 4, 5, 7, 10, 15),
           quote_intervals=(15, 30, 60, 120), modes=(5, 15),
           policies=("static", "broker"), budgets=(24,)):
    candidates = []
    for mode in modes:
        for interval in intervals:
            for quote in quote_intervals:
                for policy in policies:
                    for budget in budgets:
                        cfg = replace(config, ma_period=mode, full_interval_minutes=interval,
                                      quote_seconds=quote, policy=policy, host_rpm=30,
                                      minute_budget=budget, focus=10, depth=1, fault="")
                        result = replay("CUSTOM", cfg)
                        freshness = result["freshness_s"]
                        demand = math.ceil(cfg.universe / 100) * (1 / mode + 1 / interval) + 60 / cfg.focus_seconds
                        slo = result["slo"]
                        stable = (demand <= budget and result["host_repair"]["missed_cadence_slots"] == 0
                                  and not result["requests"]["pending"])
                        attainable = all(slo[k] for k in ("snapshot_60s", "ma55_5min", "pipeline_1730",
                                                         "attention_120s", "no_vendor_429")) and stable
                        candidates.append(dict(ma_period=mode, interval_minutes=interval,
                            quote_seconds=quote, policy=policy, minute_budget=budget,
                            static_host_minute=cfg.static_host_minute,
                            full_minute=True, focus=10, depth=1, minute_demand_rpm=round(demand, 4),
                            cadence_sustainable=stable, feasible_except_full_1m_slo=attainable,
                            feasible_all_numeric=attainable and slo["full_1m_5min"],
                            ma55_p95_s=freshness["ma55_bar"]["p95"],
                            full_1m_p95_s=freshness["full_1m"]["p95"],
                            snapshot_p95_s=freshness["snapshot"]["p95"],
                            cache_write_MB=result["estimated_volume"]["cache_write_MB"],
                            pending=result["requests"]["pending"],
                            missed_slots=result["host_repair"]["missed_cadence_slots"],
                            slo=slo, operational_status="NOT_VERIFIED"))
    eligible = [r for r in candidates if r["feasible_all_numeric"]]
    if not eligible:
        eligible = [r for r in candidates if r["feasible_except_full_1m_slo"]]
    # Minimize measured ages and write volume; retain every nondominated result.
    objectives = ("snapshot_p95_s", "ma55_p95_s", "full_1m_p95_s", "cache_write_MB")

    def dominates(a, b):
        aa = [a[k] if a[k] is not None else math.inf for k in objectives]
        bb = [b[k] if b[k] is not None else math.inf for k in objectives]
        return all(x <= y for x, y in zip(aa, bb)) and any(x < y for x, y in zip(aa, bb))

    pareto = [r for r in eligible if not any(dominates(other, r) for other in eligible)]

    def best_for(mode):
        rows = [r for r in eligible if r["ma_period"] == mode]
        return min(rows, key=lambda r: (r["full_1m_p95_s"] or math.inf,
                                       r["snapshot_p95_s"] or math.inf,
                                       r["cache_write_MB"])) if rows else None

    return dict(candidate_count=len(candidates), candidates=candidates, pareto=pareto,
                feasible_all_numeric=sum(r["feasible_all_numeric"] for r in candidates),
                feasible_except_full_1m_slo=sum(r["feasible_except_full_1m_slo"] for r in candidates),
                best_existing_5m=best_for(5), best_experimental_15m=best_for(15),
                binding_constraint="ATOMIC_FULL_1M_QUERY_END_AND_SHARED_MINUTE_BUDGET",
                source_status="CLASSIFICATION_SOURCE_MISSING; FACTOR_COVERAGE_NOT_GUARANTEED")
