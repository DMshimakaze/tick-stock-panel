"""Installable TSP external plugin: QuantDash 0.1.0 personal subscription.

Supported capabilities: daily, minute, realtime, adj_factor, depth5, full_minute.
Full-market minute persistence uses TSP's get_minute repair-round contract and
the same batched 1m endpoint and rolling request budget as historical minutes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import importlib.util
import logging
import os
from types import SimpleNamespace

import polars as pl

from app import secrets_store
from app.data_providers.base import ProviderRequestError
from .client import BudgetUnavailableError, QuantDashClient, probe_candidate
from .mapping import (depth_rows, factor_rows, kline_rows, quote_rows, parse_bj_timestamp,
                      request_ms, symbol_code, audit_trade_units)

logger = logging.getLogger(__name__)
NAME = "quantdash_user"
DATASETS = ("daily", "minute", "realtime", "adj_factor", "depth5", "full_minute")
BATCH = 100
KLINE_COUNT = 5000
MINUTE_MAX_DAYS = 12
DAILY_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
MINUTE_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
FACTOR_COLUMNS = ["symbol", "trade_date", "ex_factor"]


def _get_key():
    return secrets_store.get_env_backed_secret(f"{NAME}_api_key", "QUANTDASH_TOKEN")


def availability():
    if importlib.util.find_spec("quantdash") is None:
        return False, "未安装 quantdash==0.1.0 到 TSP 后端 Python 环境"
    if not _get_key():
        return False, "尚未配置 QuantDash Key；可在插件卡片填写或设置 QUANTDASH_TOKEN"
    return True, "配置已就绪（各数据集请分别试拉）"


def probe_api_key(key):
    if not key or not isinstance(key, str):
        return False, "Key 不能为空"
    if importlib.util.find_spec("quantdash") is None:
        return False, "TSP 后端 Python 环境未安装 quantdash==0.1.0"
    return probe_candidate(key)


def _frame(rows, columns):
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).select(columns)


def _date_bounds(start_time, end_time, *, daily=False):
    if end_time is None:
        end_time = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    if start_time is None:
        start_time = end_time - timedelta(days=365 if daily else 5)
    tz = ZoneInfo("Asia/Shanghai")
    if start_time.tzinfo is not None:
        start_time = start_time.astimezone(tz).replace(tzinfo=None)
    if end_time.tzinfo is not None:
        end_time = end_time.astimezone(tz).replace(tzinfo=None)
    if start_time > end_time:
        raise ValueError("start_time must not exceed end_time")
    return start_time, end_time


def _windows(start_time, end_time, days=MINUTE_MAX_DAYS):
    """Disjoint wallclock ranges under 5000 1m bars per security per SDK call."""
    left = start_time
    while left <= end_time:
        right = min(left + timedelta(days=days), end_time)
        yield left, right
        left = right + timedelta(milliseconds=1)


def _safe_error(exc):
    status = getattr(exc, "status_code", None)
    if status is None and getattr(exc, "response", None) is not None:
        status = getattr(exc.response, "status_code", None)
    if status == 429:
        return "QuantDash 429 限流；请减少数据能力或降低实时轮询频率"
    if status in (401, 403):
        return "QuantDash 身份验证失败或数据集无访问权限"
    if isinstance(exc, (ValueError, ProviderRequestError, BudgetUnavailableError)):
        # Locally authored messages only; avoid returning vendor URLs or credentials.
        msg = str(exc)
        allowed = ("QuantDash ", "unrequested ", "minute Kline", "daily Kline",
                   "invalid ", "duplicate ", "unsupported ", "missing ",
                   "nonpositive ", "non-finite ", "negative ", "start_time ")
        if msg.startswith(allowed):
            return msg
    return f"QuantDash SDK request failed ({type(exc).__name__})"


class QuantDashProvider:
    name = NAME
    minute_history_days = 20  # capability hint; actual history depends on subscription

    def __init__(self):
        self.config = SimpleNamespace(datasets=dict.fromkeys(DATASETS, True))
        self._key = _get_key()
        if not self._key:
            raise ValueError("QuantDash key not configured")
        self._api = QuantDashClient(self._key)
        self._etf_permission_error = None

    def close(self):
        self._api.close()

    def _klines(self, symbols, start, end, *, period):
        return self._api.call(lambda sdk: sdk.klines.batch(
            symbols, period=period,
            start_time=request_ms(start),
            end_time=request_ms(end, end_of_day=(period == "1d")),
            count=KLINE_COUNT,
            adjust="none", to_dataframe=False,
            batch_size=BATCH, max_workers=1, show_progress=False,
        ))

    def iter_daily(self, symbols, start_time=None, end_time=None,
                   asset_type="stock", on_chunk_done=None):
        start_time, end_time = _date_bounds(start_time, end_time, daily=True)
        symbols = [symbol_code(s) for s in symbols]
        chunks = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]
        for n, chunk in enumerate(chunks, start=1):
            try:
                raw = self._klines(chunk, start_time, end_time, period="1d")
                rows = kline_rows(raw, freq="daily")
                # Never accept unrequested symbols or adjusted/double-scaled records.
                if any(r["symbol"] not in chunk for r in rows):
                    raise ValueError("unrequested symbol in daily response")
                if any(not start_time.date() <= r["date"] <= end_time.date() for r in rows):
                    raise ValueError("daily Kline response outside requested dates")
                # Index points are not the traded prices of their constituents.
                if asset_type != "index":
                    audit_trade_units(rows)
                if rows and all(r["amount"] is None for r in rows):
                    logger.warning("QuantDash daily amount absent; TSP amount-dependent indicators will be incomplete")
                frame = _frame(rows, DAILY_COLUMNS)
            except Exception as exc:
                logger.warning("QuantDash daily batch %s/%s failed: %s", n, len(chunks), _safe_error(exc))
                raise ProviderRequestError(_safe_error(exc)) from None
            finally:
                if on_chunk_done:
                    on_chunk_done(n, len(chunks))
            if not frame.is_empty():
                yield frame

    def get_daily(self, symbols, start_time=None, end_time=None, asset_type="stock", on_chunk_done=None):
        frames = list(self.iter_daily(symbols, start_time, end_time, asset_type, on_chunk_done))
        return pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()

    def get_minute(self, symbols, start_time=None, end_time=None,
                   asset_type="stock", on_chunk_done=None, freq="1m"):
        if freq != "1m":
            raise ProviderRequestError("QuantDash TSP adapter only supports 1m for minute dataset")
        start_time, end_time = _date_bounds(start_time, end_time)
        symbols = [symbol_code(s) for s in symbols]
        chunks = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]
        windows = list(_windows(start_time, end_time))
        total = len(chunks) * len(windows)
        frames = []
        done = 0
        for window_start, window_end in windows:
            for chunk in chunks:
                try:
                    raw = self._klines(chunk, window_start, window_end, period="1m")
                    rows = kline_rows(raw, freq="minute")
                    if any(r["symbol"] not in chunk for r in rows):
                        raise ValueError("unrequested symbol in minute response")
                    if any(not window_start <= r["datetime"] <= window_end for r in rows):
                        raise ValueError("minute Kline response outside requested time range")
                    if asset_type != "index":
                        audit_trade_units(rows)
                    if rows and all(r["amount"] is None for r in rows):
                        logger.warning("QuantDash minute amount absent; chart average-price line may be unavailable")
                    frames.append(_frame(rows, MINUTE_COLUMNS))
                except Exception as exc:
                    logger.warning("QuantDash minute batch failed: %s", _safe_error(exc))
                    raise ProviderRequestError(_safe_error(exc)) from None
                finally:
                    done += 1
                    if on_chunk_done:
                        on_chunk_done(done, total)
        nonempty = [df for df in frames if not df.is_empty()]
        if not nonempty:
            return pl.DataFrame()
        combined = pl.concat(nonempty, how="vertical_relaxed")
        return combined.unique(subset=["symbol", "datetime"], keep="last").sort(["symbol", "datetime"])

    def get_realtime(self):
        try:
            df = self._api.call(lambda sdk: sdk.quotes.get(universes="CN_Stock", to_dataframe=True),
                                realtime=True)
            rows = quote_rows(df)
            audit_trade_units(rows)
            if df is not None and hasattr(df, "empty") and not df.empty and not rows:
                raise ValueError("non-empty snapshot mapped to no rows")
        except Exception as exc:
            logger.warning("QuantDash realtime fetch failed: %s", _safe_error(exc))
            return []
        if self._etf_permission_error is None:
            try:
                etfs = self._api.call(lambda sdk: sdk.quotes.get(universes="CN_ETF", to_dataframe=True),
                                     realtime=True)
                etf_rows = quote_rows(etfs)
                audit_trade_units(etf_rows)
                if not etf_rows:
                    raise ValueError("QuantDash ETF realtime returned no valid rows")
                rows.extend(etf_rows)
            except Exception as exc:  # noqa: BLE001 - ETF SDK failure must preserve successful stock quotes.
                error = _safe_error(exc)
                if getattr(exc, "status_code", None) in (401, 403):
                    self._etf_permission_error = error
                logger.warning("QuantDash ETF realtime unavailable; stock snapshot retained: %s", error)
        return rows

    def get_realtime_indices(self, symbols):
        if not symbols:
            return []
        try:
            syms = [symbol_code(s) for s in symbols]
            df = self._api.call(lambda sdk: sdk.quotes.get(symbols=syms, to_dataframe=True), realtime=True)
            return quote_rows(df)
        except Exception as exc:
            logger.warning("QuantDash index quotes unavailable: %s", _safe_error(exc))
            return None

    def get_adj_factors(self, symbols, start_time=None, end_time=None,
                        asset_type="stock", on_chunk_done=None):
        result = self.audit_adj_factors(symbols, start_time, end_time,
                                        asset_type, on_chunk_done)
        if "UNVERIFIED" in result["statuses"].values():
            raise ProviderRequestError("QuantDash incomplete factor coverage; missing symbol response")
        return result["factors"]

    def audit_adj_factors(self, symbols, start_time=None, end_time=None,
                          asset_type="stock", on_chunk_done=None):
        """Keep explicit empty symbol entries distinct from omitted coverage.

        The SDK has no count/pagination limit for ex_factors. Request the complete
        event chain in one HTTP call per <=100 symbols, then select the local
        history window. This also picks up corrections to older events.
        """
        if asset_type not in ("stock", "etf"):
            raise ProviderRequestError("QuantDash factors only support stocks and ETFs")
        symbols = [symbol_code(s) for s in symbols]
        chunks = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]
        frames = []
        statuses = {}
        start_day = parse_bj_timestamp(start_time).date() if start_time else None
        end_day = parse_bj_timestamp(end_time).date() if end_time else None
        for i, chunk in enumerate(chunks, 1):
            try:
                raw = self._api.call(lambda sdk: sdk.klines.ex_factors(
                    chunk, to_dataframe=False, batch_size=BATCH, max_workers=1,
                ))
                if not isinstance(raw, dict):
                    raise ValueError("invalid factor coverage response")
                if any(code not in chunk for code in raw):
                    raise ValueError("unrequested factor symbol")
                rows = []
                for code in chunk:
                    if code not in raw or raw[code] is None:
                        statuses[code] = "UNVERIFIED"
                        continue
                    events = factor_rows([{"symbol": code, **event} for event in raw[code]])
                    if any(r["symbol"] != code for r in events):
                        raise ValueError("unrequested factor symbol")
                    events = [r for r in events if
                              (start_day is None or r["trade_date"] >= start_day) and
                              (end_day is None or r["trade_date"] <= end_day)]
                    statuses[code] = "HAS_EVENTS" if events else "CONFIRMED_NO_EVENTS"
                    rows.extend(events)
                frames.append(_frame(rows, FACTOR_COLUMNS))
            except Exception as exc:
                logger.warning("QuantDash adj_factor batch failed: %s", _safe_error(exc))
                raise ProviderRequestError(_safe_error(exc)) from None
            finally:
                if on_chunk_done:
                    on_chunk_done(i, len(chunks))
        good = [df for df in frames if not df.is_empty()]
        return {"factors": pl.concat(good, how="vertical_relaxed") if good else pl.DataFrame(),
                "statuses": statuses}

    def get_depth_batch(self, symbols):
        symbols = [symbol_code(s) for s in symbols]
        result = {}
        failed = []
        skipped = []
        for index, symbol in enumerate(symbols):
            try:
                # The personal plan grants single-depth access, but rejects batch.
                # Keep TSP's batch contract and charge each stock to the shared budget.
                raw = self._api.call(lambda sdk, code=symbol: sdk.depth.get(code), wait=False)
                book = depth_rows({symbol: raw}, [symbol])[symbol]
                if not any(book["bid_prices"] + book["ask_prices"]) or not any(book["bid_volumes"] + book["ask_volumes"]):
                    raise ValueError("QuantDash empty depth book")
                result[symbol] = book
            except BudgetUnavailableError:
                skipped = symbols[index:]
                break
            except Exception as exc:
                failed.append(symbol)
                logger.warning("QuantDash depth for %s failed: %s", symbol, _safe_error(exc))
                if getattr(exc, "status_code", None) in (401, 429):
                    skipped = symbols[index + 1:]
                    break
        if failed or skipped:
            logger.warning("QuantDash depth incomplete: requested=%d received=%d failed=%d skipped=%d",
                           len(symbols), len(result), len(failed), len(skipped))
        if symbols and not result:
            raise ProviderRequestError(
                f"QuantDash depth unavailable: requested={len(symbols)} failed={len(failed)} "
                f"skipped={len(skipped)}; small queries only, background budget shared with minutes"
            )
        return result

    def test_dataset(self, dataset, symbols=None):
        samples = [symbol_code(s) for s in (symbols or ["600519.SH"])][:2]
        out = {"provider": self.name, "dataset": dataset, "rows": 0,
               "columns": [], "preview": []}
        try:
            now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            if dataset == "daily":
                data = self.get_daily(samples, now - timedelta(days=15), now)
            elif dataset == "minute":
                data = self.get_minute(samples, now - timedelta(days=3), now)
            elif dataset == "full_minute":
                beijing_now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
                data = self.get_minute(
                    samples,
                    beijing_now.replace(hour=0, minute=0, second=0, microsecond=0),
                    beijing_now,
                )
            elif dataset == "realtime":
                # A test of realtime contract must include the full-market endpoint.
                result = self.get_realtime()
                if not result:
                    raise ValueError("empty/failed universe snapshot")
                data = _frame(result[:5], list(result[0]))
                out["rows"] = len(result)
                if self._etf_permission_error:
                    out["warnings"] = [f"ETF 实时未启用：{self._etf_permission_error}"]
            elif dataset == "adj_factor":
                data = self.get_adj_factors(samples, now - timedelta(days=365), now)
            elif dataset == "depth5":
                result = self.get_depth_batch(samples)
                out["rows"] = len(result)
                out["columns"] = ["symbol", "bid_prices", "bid_volumes", "ask_prices", "ask_volumes", "timestamp"]
                out["preview"] = [{"symbol": code, **r} for code, r in result.items()][:2]
                out["requested_symbols"] = samples
                out["missing_symbols"] = [s for s in samples if s not in result]
                if out["missing_symbols"]:
                    out["warnings"] = ["五档仅返回部分标的；缺失标的可能无盘口、无权限或请求预算不足"]
                return out
            else:
                out["error"] = "unsupported/undeclared QuantDash dataset"
                return out
            out["rows"] = out["rows"] or data.height
            out["columns"] = data.columns
            for row in data.head(5).to_dicts():
                out["preview"].append({k: (v.isoformat() if hasattr(v, "isoformat") else v)
                                       for k, v in row.items()})
        except Exception as exc:
            out["error"] = _safe_error(exc)
        return out
