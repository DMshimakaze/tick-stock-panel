"""Compare shared-budget vendor references with TSP projection and API prices.

Run with DATA_DIR pointing to a temporary directory to isolate host imports.
All market samples and evidence stay in the explicitly selected output folder.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
import polars as pl

from app.indicators.pipeline import _apply_adj_factor, filter_halt_days
from app.parquet import scan_daily_parquet, scan_enriched_parquet
from app.services.adjustment_audit import collect_universe


def verify_local_projection(root: Path, output_dir: Path) -> dict:
    """Check every held daily bar against the official projection, no network."""
    report = {}
    for asset, daily_dir, enriched_dir, factor_dir in (
        ("stock", "kline_daily", "kline_daily_enriched", "adj_factor"),
        ("etf", "kline_etf_daily", "kline_etf_enriched", "adj_factor_etf"),
    ):
        universe = collect_universe(root, asset)
        state = json.loads((root / factor_dir / "audit.json").read_text(encoding="utf-8"))
        statuses = pl.DataFrame([{"symbol": r["symbol"], "status": state["records"][r["symbol"]]["status"]} for r in universe])
        factors = pl.read_parquet(root / factor_dir / "all.parquet")
        raw = filter_halt_days(scan_daily_parquet(sorted((root / daily_dir).glob("date=*/*.parquet"))).collect())
        expected = _apply_adj_factor(raw.with_columns(pl.col("close").alias("_raw_close")), factors)
        stored = scan_enriched_parquet(sorted((root / enriched_dir).glob("date=*/*.parquet"))).select(
            "symbol", "date", "open", "high", "low", "close", "raw_close",
        ).collect()
        compared = expected.join(stored, on=["symbol", "date"], suffix="_stored")
        errors = {c: (compared[c] - compared[c + "_stored"]).abs().max() for c in ("open", "high", "low", "close")}
        missing = expected.select("symbol", "date").join(stored.select("symbol", "date"), on=["symbol", "date"], how="anti")
        bad = compared.filter(pl.any_horizontal([
            (pl.col(c) - pl.col(c + "_stored")).abs() > 1e-8 for c in ("open", "high", "low", "close")
        ]))
        event_symbols = set(factors["symbol"])
        affected = expected.filter(pl.col("symbol").is_in(event_symbols))
        report[asset] = {"universe": len(universe), "coverage": statuses.group_by("status").len().to_dicts(),
                         "events": factors.height, "event_start": str(factors["trade_date"].min()),
                         "event_end": str(factors["trade_date"].max()), "valid_raw_rows": raw.height,
                         "stored_rows": stored.height, "compared_rows": compared.height,
                         "missing_enriched_rows": missing.height, "price_mismatches": bad.height,
                         "max_ohlc_abs_error": errors,
                         "affected_symbols_with_history": affected["symbol"].n_unique(),
                         "affected_history_rows": affected.height,
                         "by_exchange": [{"exchange": exchange, "symbols": sum(r["exchange"] == exchange for r in universe)}
                                         for exchange in sorted({r["exchange"] for r in universe})]}
        if bad.height:
            report[asset]["mismatch_examples"] = [{"symbol": r["symbol"], "date": str(r["date"])} for r in bad.head(10).to_dicts()]
        jumps = expected.sort(["symbol", "date"]).with_columns(
            pl.col("_raw_close").shift(1).over("symbol").alias("_previous_raw"),
            pl.col("close").shift(1).over("symbol").alias("_previous_adjusted"),
        ).with_columns(
            (pl.col("_raw_close") / pl.col("_previous_raw") - 1).alias("raw_change"),
            (pl.col("close") / pl.col("_previous_adjusted") - 1).alias("adjusted_change"),
            ((pl.col("close") / pl.col("_raw_close") - pl.col("_previous_adjusted") / pl.col("_previous_raw")).abs() > 1e-12).alias("event_between_bars"),
        ).filter(pl.col("raw_change").abs() >= 0.20).join(statuses, on="symbol", how="left")
        jumps = jumps.with_columns(
            pl.when(pl.col("status") == "FAILED").then(pl.lit("源数据异常"))
            .when(pl.col("status") == "UNVERIFIED").then(pl.lit("事件缺失/因子未取全：成因未认定"))
            .when(pl.col("event_between_bars") & (pl.col("adjusted_change").abs() < 0.20)).then(pl.lit("有除权事件且已消除"))
            .otherwise(pl.lit("真实市场波动/尚未核实")).alias("classification"),
        )
        jumps.select("symbol", "date", "raw_change", "adjusted_change", "status", "classification").write_csv(output_dir / f"{asset}_price_jumps.csv")
        report[asset]["price_jump_threshold"] = 0.20
        report[asset]["price_jump_candidates"] = jumps.height
        report[asset]["price_jump_classifications"] = jumps.group_by("classification").len().to_dicts()
        # Compare both projection implementations on the actual held minutes.
        from app.services.minute_adjust import apply_minute_adjustment

        minute_files = sorted((root / ("kline_etf_minute" if asset == "etf" else "kline_minute")).glob("date=*/*.parquet"))
        if asset == "etf":
            minute_files += sorted((root / "kline_minute").glob("date=*/*.parquet"))
        if minute_files:
            minute = pl.scan_parquet(minute_files).filter(pl.col("symbol").is_in({r["symbol"] for r in universe})).collect()
            if not minute.is_empty():
                projected = apply_minute_adjustment(minute.with_columns(pl.col("close").alias("_raw_minute_close")), root, asset)
                ratios = expected.select("symbol", "date", (pl.col("close") / pl.col("_raw_close")).alias("_ratio"))
                joined = projected.with_columns(pl.col("datetime").dt.date().alias("date")).join(ratios, on=["symbol", "date"])
                report[asset]["minute_rows_compared"] = joined.height
                report[asset]["minute_daily_max_price_error"] = (joined["close"] - joined["_raw_minute_close"] * joined["_ratio"]).abs().max()
    return report


def verify_reference(payload: dict) -> tuple[dict, pl.DataFrame]:
    raw = pl.DataFrame(payload["raw"]).with_columns(pl.col("date").str.to_date())
    forward = pl.DataFrame(payload["forward"]).with_columns(pl.col("date").str.to_date())
    factors = pl.DataFrame(payload["factors"])
    if not factors.is_empty():
        factors = factors.with_columns(pl.col("trade_date").str.to_date())
    projected = _apply_adj_factor(raw, factors)
    joined = projected.join(forward, on=["symbol", "date"], suffix="_source")
    if joined.height != raw.height or joined.height != forward.height:
        raise ValueError("源原始日K与源前复权日K的日期覆盖不一致")
    summary = {}
    for symbol in raw["symbol"].unique().sort():
        rows = joined.filter(pl.col("symbol") == symbol).sort("date")
        events = factors.filter(pl.col("symbol") == symbol) if not factors.is_empty() else factors
        errors = {c: (rows[c] - rows[c + "_source"]).abs().max() for c in ("open", "high", "low", "close")}
        boundaries = []
        dates = rows["date"].to_list()
        for event in events.iter_rows(named=True):
            center = next((i for i, day in enumerate(dates) if day >= event["trade_date"]), len(dates))
            window = rows.slice(max(0, center - 10), 21)
            boundaries.append({"event": str(event["trade_date"]), "factor": event["ex_factor"],
                               "window_start": str(window["date"].min()), "window_end": str(window["date"].max()),
                               "rows": window.height,
                               "max_close_error": (window["close"] - window["close_source"]).abs().max()})
        latest = rows.tail(1)
        raw_latest = raw.filter(pl.col("symbol") == symbol).sort("date").tail(1)
        summary[symbol] = {"rows": rows.height, "start": str(rows["date"].min()),
                           "end": str(rows["date"].max()), "events": events.height,
                           "raw_rows": raw.filter(pl.col("symbol") == symbol).height,
                           "forward_rows": forward.filter(pl.col("symbol") == symbol).height,
                           "source_missing_rows": raw.filter(pl.col("symbol") == symbol).height - rows.height,
                           "max_ohlc_abs_error": errors, "boundaries": boundaries,
                           "latest_anchor_error": abs(latest["close"][0] - raw_latest["close"][0])}
        if max(errors.values()) > 1e-7:
            raise ValueError(f"{symbol} TSP 与源前复权 OHLC 不一致: {errors}")
    return summary, projected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3018")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reuse-references", action="store_true", help="复用本次已下载的参考证据，补跑本地/API 比较")
    parser.add_argument("--data-dir", type=Path, help="额外核验全库原始日K→enriched 及实际分钟的投影一致性")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    with httpx.Client(base_url=args.base_url, timeout=180) as client:
        for offset in range(0, len(args.symbols), 2):
            symbols = args.symbols[offset:offset + 2]
            evidence = args.output_dir / f"reference_{offset}.json"
            if args.reuse_references and evidence.exists():
                payload = json.loads(evidence.read_text(encoding="utf-8"))
            else:
                response = client.post("/api/settings/data-sources/test", json={
                    "provider": "quantdash_user", "dataset": "adjustment_reference", "symbols": symbols,
                })
                response.raise_for_status()
                payload = response.json()
                evidence.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            if payload.get("error"):
                raise ValueError(payload["error"])
            summary, projected = verify_reference(payload)
            for symbol in symbols:
                expected = projected.filter(pl.col("symbol") == symbol)
                api = client.get("/api/kline/daily", params={
                    "symbol": symbol, "start_date": str(expected["date"].min()),
                    "end_date": str(expected["date"].max()), "days": 2000,
                })
                api.raise_for_status()
                api_rows = api.json()["rows"]
                columns = ["symbol", "date", "open", "high", "low", "close", "ma5", "ma10", "ma20", "ma60"]
                rows = pl.DataFrame([{k: r.get(k) for k in columns} for r in api_rows], infer_schema_length=None).with_columns(pl.col("date").str.to_date())
                compared = expected.join(rows, on=["symbol", "date"], suffix="_api")
                summary[symbol]["api_rows_compared"] = compared.height
                summary[symbol]["api_rows"] = rows.height
                summary[symbol]["reference_rows_outside_api_history"] = expected.height - compared.height
                summary[symbol]["api_rows_without_reference"] = rows.height - compared.height
                summary[symbol]["api_max_ohlc_abs_error"] = {
                    c: (compared[c] - compared[c + "_api"]).abs().max() if compared.height else None
                    for c in ("open", "high", "low", "close")
                }
                summary[symbol]["api_max_close_error"] = (compared["close"] - compared["close_api"]).abs().max() if compared.height else None
                if compared.height and max(summary[symbol]["api_max_ohlc_abs_error"].values()) > 1e-7:
                    raise ValueError(f"{symbol} API 前复权价格与源参考不一致")
                means = {}
                for period in (5, 10, 20, 60):
                    ma = f"ma{period}"
                    delta = (rows["close"].rolling_mean(period) - rows[ma]).abs().drop_nulls()
                    means[ma] = delta.max() if len(delta) else None
                summary[symbol]["api_ma_errors"] = means
                if any(value is not None and value > 1e-7 for value in means.values()):
                    raise ValueError(f"{symbol} API 均线与前复权收盘价尺度不一致")
            report.update(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    (args.output_dir / "sample_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.data_dir:
        local = verify_local_projection(args.data_dir, args.output_dir)
        (args.output_dir / "local_projection_verification.json").write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(local, ensure_ascii=False), flush=True)
        if any(item["price_mismatches"] or item["missing_enriched_rows"] for item in local.values()):
            raise SystemExit("全库原始日K→enriched 投影仍存在差异，详情见本地验收 JSON")


if __name__ == "__main__":
    main()
