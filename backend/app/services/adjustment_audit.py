"""Full local-universe factor coverage and resumable enriched recomputation.

Prices are computed only by indicators.pipeline; raw daily/minute files are read
here, never rewritten. Requests use the loaded provider and its shared budget.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from app.data_providers import custom
from app.data_providers.base import ProviderRequestError
from app.indicators.pipeline import recompute_daily_symbols
from app.market_time import cn_now
from app.parquet import scan_parquet_compat
from app.services import kline_sync, preferences
from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)
VERIFIED = kline_sync.VERIFIED_FACTOR_STATES
BATCH_SIZE = 100


def collect_universe(data_dir: Path, asset_type: str) -> list[dict]:
    """Dimension union raw daily/minute symbols, including historical-only codes."""
    if asset_type not in {"stock", "etf"}:
        raise ValueError("因子审计仅支持股票和 ETF")
    dimension = "instruments_etf" if asset_type == "etf" else "instruments"
    daily = "kline_etf_daily" if asset_type == "etf" else "kline_daily"
    minute = "kline_etf_minute" if asset_type == "etf" else "kline_minute"
    universe: dict[str, dict] = {}
    for file in sorted((data_dir / dimension).glob("*.parquet")):
        for symbol in pl.read_parquet(file, columns=["symbol"])["symbol"].to_list():
            universe[symbol] = {"symbol": symbol, "dimension": True}
    etf_files = sorted((data_dir / "instruments_etf").glob("*.parquet"))
    index_files = sorted((data_dir / "instruments_index").glob("*.parquet"))
    etfs = set(pl.read_parquet(etf_files, columns=["symbol"])["symbol"]) if etf_files else set()
    indices = set(pl.read_parquet(index_files, columns=["symbol"])["symbol"]) if index_files else set()
    sources = [("daily", daily, "date"), ("minute", minute, "datetime")]
    if asset_type == "etf":
        # Existing ETF minutes may live in the old stock directory. Classify by
        # dimensions and leave those original partitions in place.
        sources.append(("minute", "kline_minute", "datetime"))
    for kind, table, column in sources:
        files = sorted((data_dir / table).glob("date=*/*.parquet"))
        if not files:
            continue
        scan = scan_parquet_compat(files, hive_partitioning=False).select("symbol", column)
        if asset_type == "stock":
            scan = scan.filter(~pl.col("symbol").is_in(etfs | indices))
        elif table == "kline_minute":
            scan = scan.filter(pl.col("symbol").is_in(etfs))
        spans = (
            scan
            .group_by("symbol")
            .agg(pl.col(column).min().cast(pl.Date).alias("start"),
                 pl.col(column).max().cast(pl.Date).alias("end"), pl.len().alias("rows"))
            .collect(engine="streaming")
        )
        for row in spans.iter_rows(named=True):
            record = universe.setdefault(row["symbol"], {"symbol": row["symbol"], "dimension": False})
            record[kind + "_start"] = min(record.get(kind + "_start", row["start"].isoformat()), row["start"].isoformat())
            record[kind + "_end"] = max(record.get(kind + "_end", row["end"].isoformat()), row["end"].isoformat())
            record[kind + "_rows"] = record.get(kind + "_rows", 0) + row["rows"]
    for record in universe.values():
        starts = [record[k] for k in ("daily_start", "minute_start") if k in record]
        record["history_start"] = min(starts) if starts else None
        record["exchange"] = record["symbol"].rsplit(".", 1)[-1]
    return [universe[s] for s in sorted(universe)]


def sync_asset_factors(repo, *, asset_type: str, on_progress=None, refresh=False, retry_only=False) -> dict:
    """Audit all symbols, persist unfinished work, then rebuild changed histories.

    A verified same-day symbol is reused; unfinished symbols remain retryable.
    retry_only excludes verified symbols even on subsequent days. Normally the source
    is queried again; QuantDash supplies the entire event chain at the same HTTP
    cost, so old corrections are included rather than hidden by a recent window.
    """
    root = Path(repo.store.data_dir)
    provider_name = preferences.get_adj_factor_provider()
    provider = custom.get_provider(provider_name)
    audit = getattr(provider, "audit_adj_factors", None)
    if not callable(audit):
        raise ProviderRequestError(f"数据源 {provider_name} 未提供逐证券因子覆盖确认")
    now = cn_now()
    if now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 10):
        raise ProviderRequestError("全市场因子审计仅在盘后空闲时运行")
    today = now.date().isoformat()
    universe = collect_universe(root, asset_type)
    by_symbol = {r["symbol"]: r for r in universe}
    folder = root / ("adj_factor_etf" if asset_type == "etf" else "adj_factor")
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "audit.json"
    state = kline_sync.read_adj_factor_coverage(root, asset_type)
    if state.get("source") != provider_name:
        state = {"source": provider_name, "records": {}, "pending_recompute": state.get("pending_recompute", [])}
    records = state.setdefault("records", {})
    pending = set(state.get("pending_recompute", []))

    def save():
        state["pending_recompute"] = sorted(pending)
        atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2))

    for item in universe:
        record = records.setdefault(item["symbol"], {"status": "UNVERIFIED", "attempts": 0})
        # Extending local history requires re-verifying the expanded window.
        if record.get("history_start") != item["history_start"]:
            record["status"] = "UNVERIFIED"
            record["through"] = None
            record["history_start"] = item["history_start"]
    requested = [r for r in universe
                 if (not retry_only or records[r["symbol"]]["status"] not in VERIFIED)
                 and (refresh or records[r["symbol"]].get("through") != today
                      or records[r["symbol"]]["status"] in {"UNVERIFIED", "FAILED"})]
    batches = [requested[i:i + BATCH_SIZE] for i in range(0, len(requested), BATCH_SIZE)]
    added = corrected = calls = retries = 0
    changed: set[str] = set()
    save()
    for number, batch in enumerate(batches, 1):
        if on_progress:
            on_progress(number - 1, len(batches))
        symbols = [r["symbol"] for r in batch]
        # No local bars means request the complete available vendor history.
        start = (datetime.fromisoformat(min(r["history_start"] for r in batch))
                 if all(r["history_start"] for r in batch) else None)
        for symbol in symbols:
            record = records[symbol]
            retries += "error" in record
            record["attempts"] += 1
            record["status"] = "UNVERIFIED"
            record["through"] = None
        save()
        calls += 1
        try:
            result = audit(symbols, start_time=start, end_time=now, asset_type=asset_type)
            factors = result["factors"]
            statuses = result["statuses"]
            existing = pl.read_parquet(folder / "all.parquet") if (folder / "all.parquet").exists() else pl.DataFrame()
            selected = []
            for item in batch:
                symbol = item["symbol"]
                events = (factors.filter(pl.col("symbol") == symbol) if not factors.is_empty()
                          else pl.DataFrame())
                if item["history_start"] and not events.is_empty():
                    events = events.filter(pl.col("trade_date") >= date.fromisoformat(item["history_start"]))
                status = statuses.get(symbol, "UNVERIFIED")
                coverage_error = result.get("errors", {}).get(symbol)
                if status in VERIFIED:
                    status = "HAS_EVENTS" if not events.is_empty() else "CONFIRMED_NO_EVENTS"
                    previous = existing.filter(pl.col("symbol") == symbol) if not existing.is_empty() else pl.DataFrame()
                    if item["history_start"] and not previous.is_empty():
                        previous = previous.filter(pl.col("trade_date") >= date.fromisoformat(item["history_start"]))
                    # A disappearing event may be vendor truncation or a retraction.
                    # Preserve it and expose the disagreement for review.
                    if not previous.is_empty() and (events.is_empty() or previous.join(
                        events, on=["symbol", "trade_date"], how="anti",
                    ).height):
                        status = "UNVERIFIED"
                        coverage_error = "源响应缺少已保存事件；旧事件保留，撤销未确认"
                    if not events.is_empty():
                        selected.append(events)
                records[symbol].update(status=status, events=events.height,
                                       history_start=item["history_start"], through=today)
                if status in {"FAILED", "UNVERIFIED"}:
                    records[symbol]["error"] = coverage_error or (
                        "无效的源事件因子" if status == "FAILED" else "源未返回该证券的明确事件列表"
                    )
                else:
                    records[symbol].pop("error", None)
            incoming = pl.concat(selected, how="diagonal_relaxed") if selected else pl.DataFrame()
            if not incoming.is_empty():
                incoming = incoming.with_columns(pl.lit(provider_name).alias("source"),
                                                 pl.lit(now.isoformat()).alias("updated_at"))
                out = folder / "all.parquet"
                if not existing.is_empty():
                    corrected += incoming.join(
                        existing.select("symbol", "trade_date", pl.col("ex_factor").alias("_old")),
                        on=["symbol", "trade_date"], how="inner",
                    ).filter((pl.col("ex_factor") - pl.col("_old")).abs() > 1e-9).height

                def remember_changed(symbols_changed):
                    changed.update(symbols_changed)
                    pending.update(s for s in symbols_changed if by_symbol[s].get("daily_start"))
                    # Persist before factor publication: interruption must never lose rebuild work.
                    save()

                new_count, _ = kline_sync._merge_adj_factor_store(out, incoming, on_changed=remember_changed)
                added += new_count
            save()
        except (ProviderRequestError, ValueError, OSError) as exc:
            logger.warning("%s factor audit batch %d failed: %s", asset_type, number, exc)
            for symbol in symbols:
                records[symbol].update(status="FAILED", error=str(exc), through=today)
            save()
            if "429" in str(exc):
                break
        if on_progress:
            on_progress(number, len(batches))

    to_recompute = [s for s in sorted(pending) if s in by_symbol and by_symbol[s].get("daily_start")]
    rows = 0
    completed = set()

    def remember_completed(symbols_done):
        completed.update(symbols_done)
        pending.difference_update(symbols_done)
        save()

    if to_recompute:
        rows = recompute_daily_symbols(
            repo, asset_type, start=date.fromisoformat(min(by_symbol[s]["daily_start"] for s in to_recompute)),
            symbols=to_recompute, on_batch_done=on_progress, on_symbols_done=remember_completed,
        )
    save()
    counts = Counter(records[r["symbol"]]["status"] for r in universe)
    exchange_counts = {}
    for item in universe:
        group = exchange_counts.setdefault(item["exchange"], Counter())
        group[records[item["symbol"]]["status"]] += 1
    result = {"asset_type": asset_type, "universe": len(universe), "requests": calls,
            "requested_symbols": len(requested), "retries": retries,
            "counts": dict(counts), "by_exchange": {k: dict(v) for k, v in exchange_counts.items()},
            "events": sum(records[r["symbol"]].get("events", 0) for r in universe
                          if records[r["symbol"]]["status"] in VERIFIED),
            "new_events": added, "corrected_events": corrected, "changed_symbols": sorted(changed),
            "recomputed_symbols": len(completed), "recompute_attempted_symbols": len(to_recompute),
            "recomputed_rows": rows,
            "pending_recompute": sorted(pending)}
    state.setdefault("runs", []).append({"time_beijing": cn_now().isoformat(), **result})
    save()
    return result


def run_unified_sync(repo, on_progress=None, *, refresh=False, retry_only=False) -> dict:
    results = []
    for asset_type in ("stock", "etf"):
        def progress(current, total, asset=asset_type):
            if on_progress:
                on_progress("sync_adj", 10 + int(75 * current / max(1, total)),
                            f"{asset} 因子审计/历史重算 {current}/{total}", skip_log=True)
        results.append(sync_asset_factors(repo, asset_type=asset_type,
                                          on_progress=progress, refresh=refresh, retry_only=retry_only))
    return {"assets": results, "universe_size": sum(r["universe"] for r in results),
            "adj_factor_symbols": sum(len(r["changed_symbols"]) for r in results),
            "adj_written": sum(r["new_events"] for r in results),
            "enriched_rows": sum(r["recomputed_rows"] for r in results), "skipped_stages": []}
