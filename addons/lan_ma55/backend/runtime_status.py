"""Read-only business status from independently committed market and analysis facts."""

from .activation_results import display_targets
from .market_grid import SOURCE, wall
from .multiframe_core.multiframe import TIMEFRAMES

RUNTIME_FIELDS = (
    "version", "phase", "active_operation", "active_symbols", "work_stage", "waiting_for",
    "error", "publisher_error", "command_error", "host_job_status", "calendar", "deferred_bj_factors",
)


def completed_targets(schedules):
    return {period: values[-1] if values else None for period, values in schedules.items()}


def observation_summary(rows, targets, market_day, *, latest_saved=None, latest_analyzed=None):
    """Count only analyses for each period's own completed exchange target."""
    total = len(rows)
    readiness = {period: 0 if not total or targets[period] else None for period in TIMEFRAMES}
    reasons = {period: {} for period in TIMEFRAMES}
    ready_symbols = ready_all = candidates = 0
    activated = 0
    has_activations = False
    arrivals = 0 if targets["15m"] else None
    saved, analyzed = latest_saved, latest_analyzed
    for row in rows:
        if arrivals is not None and row.get("raw_15m_end") == targets["15m"]:
            arrivals += 1
        ready = 0
        candidate = False
        active = False
        reference = row.get("reference")
        if reference and (not saved or reference["at"] > saved):
            saved = reference["at"]
        minute = row["frames"].get("15m") or {}
        last_analysis = ((minute.get("result") or {}).get("latest") or {}).get("timestamp")
        if last_analysis:
            last_analysis = str(wall(last_analysis))
            if not analyzed or last_analysis > analyzed:
                analyzed = last_analysis
        for period in TIMEFRAMES:
            target = targets[period]
            if not target:
                continue
            frame = row["frames"].get(period, {})
            activation = frame.get("activation", {})
            active |= bool(activation.get("events"))
            has_activations |= activation.get("state") in ("ACTIVATED", "INACTIVE")
            status = frame.get("status") or "WAITING_INPUT"
            if frame.get("expected_end") != target:
                status = "WAITING_FOR_COMPLETED_TARGET"
            if status != "READY":
                counts = reasons[period]
                counts[status] = counts.get(status, 0) + 1
                continue
            readiness[period] += 1
            ready += 1
            latest = (frame.get("result") or {}).get("latest") or {}
            candidate |= bool(latest.get("event_id"))
        ready_symbols += bool(ready)
        ready_all += ready == len(TIMEFRAMES)
        candidates += candidate
        activated += active
    minute = readiness["15m"]
    return dict(
        market_day=market_day, target_15m=targets["15m"], expected=total, ready_symbols=ready_symbols,
        blocked_symbols=total - ready_symbols, candidate_symbols=candidates if any(
            value is not None for value in readiness.values()) else None,
        activation_symbols=activated if has_activations or activated else None,
        ready_15m=minute, unready_15m=total - minute if minute is not None else None,
        arrived_15m=arrivals,
        ready_all=ready_all if all(value is not None for value in readiness.values()) else None,
        ready_by_timeframe=readiness, blocking_by_timeframe=reasons,
        latest_saved_15m=saved, latest_analyzed_15m=analyzed,
    )


def runtime_projection(state, *, focus, scope_count, targets, market_day, published, worker_alive):
    revision = focus.snapshot["revision"] if focus else None
    run = focus.run if focus else None
    coverage = None
    watermarks = {}
    if published and (revision, run, scope_count) == published["scope"]:
        watermarks = {field: published["coverage"][field] for field in ("latest_saved_15m", "latest_analyzed_15m")}
        if targets == published["targets"]:
            coverage = published["coverage"]
    return {
        **{field: state.get(field) for field in RUNTIME_FIELDS},
        "market_day": market_day, "target_15m": targets.get("15m"),
        "activation_targets": display_targets(targets, market_day),
        "focus_revision": revision, "taxonomy_run": run, "scope_count": scope_count,
        "saved_sectors": len(focus.snapshot["sectors"]) if focus else None,
        "coverage": coverage, "worker_alive": worker_alive,
        **watermarks,
        "result_id": published["id"] if published else None,
        "result_published_ms": published["published_ms"] if published else None,
    }


ERROR_TEXT = {
    "CALENDAR_LAG": "今日交易日尚未由源端完成行情核实，保留上次有效交易日",
    "SOURCE_LAG": "部分证券尚未取得完整目标15m，保留上次有效结果",
    "FACTOR_PENDING": "部分证券复权因子未核实，暂停这些证券的新买点计算",
    "HOST_BUSY": "宿主正在执行数据任务，插件让出采集额度",
    "QUOTA_WAIT": "等待插件滚动请求额度，后台将按计划继续",
    "RATE_LIMITED": "数据源返回429，按源端要求冷却",
    "CREDENTIAL_REQUIRED": "行情凭据不可用，请检查已有数据源配置",
    "SOURCE_UNAVAILABLE": "行情请求失败，保留已保存K线和买点",
    "PUBLISH_FAILED": "结果发布失败，仍显示上次有效投影",
    "ERROR": "后台已停止处理，请查看错误详情",
}


def business_status(state, snapshot, now):
    refresh, analysis = snapshot.get("refresh", {}), snapshot.get("analysis", {})
    target = refresh.get("target_15m")
    expected = refresh.get("expected", 0)
    saved = refresh.get("latest_completed15")
    analyzed = analysis.get("latest_completed15")
    calendar = state.get("calendar") or snapshot.get("calendar", {})
    day = now[:10]
    minute = wall(now).hour * 60 + wall(now).minute
    verified = calendar.get("confirmed_day") == day
    market_session = ("CLOSED" if wall(now).weekday() >= 5 or minute >= 901
                      else "PREMARKET" if minute < 570
                      else "LUNCH" if 690 < minute < 780 else "OPEN")
    reason = None
    phase = state.get("phase")
    if state.get("publisher_error"):
        reason = "PUBLISH_FAILED"
    elif phase in ("ERROR", "CREDENTIAL_REQUIRED", "SOURCE_UNAVAILABLE", "RATE_LIMITED", "HOST_BUSY"):
        reason = phase
    elif phase == "QUOTA_WAIT" and (state.get("work_stage") != "HISTORY" or state.get("queue_size")):
        reason = phase
    elif state.get("host_job_status", {}).get("state") == "UNAVAILABLE":
        reason = state["host_job_status"].get("reason", "HOST_STATUS_UNAVAILABLE")
    elif not verified and market_session in ("OPEN", "LUNCH") and minute >= 586:
        reason = "CALENDAR_LAG"
    elif target and not state.get("queue_size") and refresh.get("refreshed", 0) < expected:
        reason = "SOURCE_LAG"
    elif target and not state.get("pending_analysis") and analysis.get("calculated", 0) < expected and snapshot.get("factor_pending", snapshot.get("states", {}).get("FACTOR_PENDING", 0)):
        reason = "FACTOR_PENDING"
    current = bool(expected and target and refresh.get("refreshed") == expected
                   and analysis.get("calculated") == expected)
    if not target or not expected or (not verified and market_session in ("OPEN", "LUNCH")):
        mode = "UNKNOWN"
    elif not snapshot.get("source_counts", {}).get(state.get("source_id", SOURCE)):
        mode = "INITIAL_HISTORY"
    elif state.get("work_stage") in ("C", "HISTORY") and not state.get("queue_size"):
        mode = "HISTORY_BACKFILL"
    elif not current:
        mode = "LIVE_CATCHUP"
    elif market_session in ("CLOSED", "PREMARKET"):
        mode = "HISTORY_BACKFILL" if snapshot.get("history_queue_count") else "MARKET_CLOSED"
    else:
        mode = "WAIT_NEXT_BAR"
    operation = state.get("active_operation", "IDLE")
    if reason in ("ERROR", "CREDENTIAL_REQUIRED", "SOURCE_UNAVAILABLE", "RATE_LIMITED") or ((reason or "").startswith("HOST_") and reason != "HOST_BUSY"):
        operation = "BLOCKED"
    message = ERROR_TEXT.get(reason)
    if reason and reason.startswith("HOST_") and not message:
        message = "宿主任务状态暂不可核实，采集暂停；按限定频率重查"
    counts = snapshot.get("asset_counts", {})
    monitoring_verified = snapshot.get("universe", {}).get("expected") is not None
    return dict(data_target_15m=target, latest_saved_15m=saved,
                latest_analyzed_15m=analyzed, active_operation=operation,
                sync_mode=mode, market_session=market_session,
                today_session_verified=verified, all_current=current,
                blocking_reason=reason, blocking_message=message,
                monitoring_verified=monitoring_verified,
                monitored_stock_count=counts.get("stock", 0) if monitoring_verified else None,
                monitored_etf_count=counts.get("etf", 0) if monitoring_verified else None,
                status_at=now)
