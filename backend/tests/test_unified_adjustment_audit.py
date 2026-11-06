"""Real repositories/pipeline with an injected, network-free factor provider."""
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers.base import ProviderRequestError
from app.market_time import CN_TZ
from app.services import adjustment_audit as audit
from app.services import kline_sync
from app.tickflow.repository import DataStore, KlineRepository

A, B, C = "600001.SH", "000001.SZ", "920001.BJ"
DAYS = [date(2026, 1, 2), date(2026, 2, 2), date(2026, 3, 2)]


def bars(symbol, prices):
    return pl.DataFrame({"symbol": [symbol] * 3, "date": DAYS,
                         **{c: prices for c in ("open", "high", "low", "close")},
                         "volume": [100.0] * 3, "amount": [p * 10000 for p in prices]})


def event(symbol=A, factor=2.0):
    return {"symbol": symbol, "trade_date": DAYS[1], "ex_factor": factor}


class Source:
    def __init__(self):
        self.calls = []
        self.events = [event()]
        self.fail = False
        self.omit = set()

    def audit_adj_factors(self, symbols, **kwargs):
        self.calls.append((symbols, kwargs))
        if self.fail:
            self.fail = False
            raise ProviderRequestError("QuantDash 429")
        rows = [r for r in self.events if r["symbol"] in symbols]
        return {"factors": pl.DataFrame(rows), "statuses": {
            s: ("HAS_EVENTS" if any(r["symbol"] == s for r in rows) else "CONFIRMED_NO_EVENTS")
            for s in symbols if s not in self.omit}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = KlineRepository(DataStore(tmp_path))
    source = Source()
    monkeypatch.setattr(audit.custom, "get_provider", lambda name: source)
    monkeypatch.setattr(audit.preferences, "get_adj_factor_provider", lambda: "quantdash_user")
    monkeypatch.setattr(audit, "cn_now", lambda: datetime(2026, 11, 6, 21, tzinfo=CN_TZ))
    return repo, source


def seed(repo, asset_type="stock"):
    root = repo.store.data_dir
    folder = root / ("instruments_etf" if asset_type == "etf" else "instruments")
    folder.mkdir(exist_ok=True)
    pl.DataFrame({"symbol": [A, B]}).write_parquet(folder / "all.parquet")
    frame = pl.concat([bars(A, [10.0, 5.0, 5.2]), bars(C, [7.0, 7.1, 7.2])])
    if asset_type == "stock":
        repo.append_daily(frame)
    else:
        repo.append_etf_daily(frame)
    return frame


def stored(repo, asset_type="stock"):
    folder = "kline_etf_enriched" if asset_type == "etf" else "kline_daily_enriched"
    return pl.read_parquet(sorted((repo.store.data_dir / folder).glob("date=*/*.parquet"))).sort(["symbol", "date"])


@pytest.mark.parametrize("asset_type", ["stock", "etf"])
def test_universe_events_rebuild_and_noop(setup, asset_type):
    repo, source = setup
    raw = seed(repo, asset_type)
    universe = audit.collect_universe(repo.store.data_dir, asset_type)
    assert {r["symbol"] for r in universe} == {A, B, C}
    assert next(r for r in universe if r["symbol"] == C)["dimension"] is False
    result = audit.sync_asset_factors(repo, asset_type=asset_type)
    assert result["counts"] == {"HAS_EVENTS": 1, "CONFIRMED_NO_EVENTS": 2}
    assert result["recomputed_symbols"] == 1
    assert result["recomputed_rows"] == 3
    got = stored(repo, asset_type).filter(pl.col("symbol") == A)
    assert got["close"].to_list() == pytest.approx([5.0, 5.0, 5.2])
    assert got["raw_close"].to_list() == [10.0, 5.0, 5.2]
    folder = "adj_factor_etf" if asset_type == "etf" else "adj_factor"
    factors = repo.store.data_dir / folder / "all.parquet"
    mtime = factors.stat().st_mtime_ns
    again = audit.sync_asset_factors(repo, asset_type=asset_type, refresh=True)
    assert again["recomputed_rows"] == 0
    assert again["corrected_events"] == 0
    assert factors.stat().st_mtime_ns == mtime
    source.events = [event(factor=4.0)]
    corrected = audit.sync_asset_factors(repo, asset_type=asset_type, refresh=True)
    assert corrected["corrected_events"] == 1
    assert corrected["recomputed_symbols"] == 1
    assert stored(repo, asset_type).filter(pl.col("symbol") == A)["close"][0] == 2.5
    raw_folder = "kline_etf_daily" if asset_type == "etf" else "kline_daily"
    remaining = pl.read_parquet(sorted((repo.store.data_dir / raw_folder).glob("date=*/*.parquet")))
    assert remaining.select(raw.columns).sort(["symbol", "date"]).equals(raw.sort(["symbol", "date"]))


def test_failure_429_resumes_only_unfinished_batches(setup, monkeypatch):
    repo, source = setup
    seed(repo)
    monkeypatch.setattr(audit, "BATCH_SIZE", 1)
    real = source.audit_adj_factors

    def fail_second(symbols, **kwargs):
        if len(source.calls) == 1:
            source.fail = True
        return real(symbols, **kwargs)

    monkeypatch.setattr(source, "audit_adj_factors", fail_second)
    first = audit.sync_asset_factors(repo, asset_type="stock")
    assert first["counts"] == {"CONFIRMED_NO_EVENTS": 1, "FAILED": 1, "UNVERIFIED": 1}
    monkeypatch.setattr(source, "audit_adj_factors", real)
    second = audit.sync_asset_factors(repo, asset_type="stock")
    assert second["requested_symbols"] == 2
    assert second["retries"] == 1
    assert second["counts"] == {"HAS_EVENTS": 1, "CONFIRMED_NO_EVENTS": 2}
    assert source.calls[-2][0] == [A]


def test_interruption_after_factor_write_preserves_pending_rebuild(setup, monkeypatch):
    repo, _ = setup
    seed(repo)
    real = audit.recompute_daily_symbols
    monkeypatch.setattr(audit, "recompute_daily_symbols", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        audit.sync_asset_factors(repo, asset_type="stock")
    assert kline_sync.read_adj_factor_coverage(repo.store.data_dir, "stock")["pending_recompute"] == [A]
    monkeypatch.setattr(audit, "recompute_daily_symbols", real)
    resumed = audit.sync_asset_factors(repo, asset_type="stock")
    assert resumed["requests"] == 0
    assert resumed["recomputed_rows"] == 3
    assert resumed["pending_recompute"] == []


def test_omitted_symbol_is_unverified_not_empty(setup):
    repo, source = setup
    seed(repo)
    source.omit = {B}
    result = audit.sync_asset_factors(repo, asset_type="stock")
    assert result["counts"]["UNVERIFIED"] == 1
    assert result["counts"]["CONFIRMED_NO_EVENTS"] == 1
    # A completed HTTP response with omitted coverage remains visible, without
    # repeatedly spending the same day's quota. Explicit refresh can recheck it.
    repeated = audit.sync_asset_factors(repo, asset_type="stock")
    assert repeated["requests"] == 0
    assert repeated["counts"]["UNVERIFIED"] == 1


@pytest.mark.parametrize("factor", [0, -1, float("nan"), float("inf")])
def test_invalid_event_never_published(tmp_path, factor):
    with pytest.raises(ValueError):
        kline_sync._merge_adj_factor_store(tmp_path / "all.parquet", pl.DataFrame([event(factor=factor)]))
    assert not (tmp_path / "all.parquet").exists()


def test_conflicting_same_day_factors_rejected(tmp_path):
    with pytest.raises(ValueError, match="冲突"):
        kline_sync._merge_adj_factor_store(tmp_path / "all.parquet", pl.DataFrame([event(), event(factor=3.0)]))


def test_single_chart_respects_custom_source_and_verified_local_empty(setup, monkeypatch):
    repo, source = setup
    seed(repo)
    audit.sync_asset_factors(repo, asset_type="stock")
    monkeypatch.setattr(kline_sync, "get_client", lambda: pytest.fail("must not call TickFlow"))
    assert kline_sync.fetch_adj_factor_single(
        B, repo=repo, start_time=datetime(2026, 1, 1), end_time=datetime(2026, 11, 6),
    ).is_empty()
    source.get_adj_factors = lambda *a, **k: pl.DataFrame([event()])
    monkeypatch.setattr(audit.custom, "provider_has_dataset", lambda *a: True)
    result = kline_sync.fetch_adj_factor_single(A, capset=SimpleNamespace(has=lambda cap: False))
    assert result["ex_factor"][0] == 2.0


def test_full_dimension_is_batched_without_network_or_price_history(setup):
    repo, source = setup
    folder = repo.store.data_dir / "instruments"
    symbols = [f"{600000 + i}.SH" for i in range(205)]
    pl.DataFrame({"symbol": symbols}).write_parquet(folder / "all.parquet")
    source.events = []
    result = audit.sync_asset_factors(repo, asset_type="stock")
    assert result["universe"] == 205
    assert [len(codes) for codes, _ in source.calls] == [100, 100, 5]
    assert all(kwargs["start_time"] is None for _, kwargs in source.calls)
    assert result["counts"] == {"CONFIRMED_NO_EVENTS": 205}
    assert result["recomputed_rows"] == 0


def test_two_events_use_full_product_for_daily_and_minute(setup):
    from app.indicators.pipeline import _apply_adj_factor
    from app.services.minute_adjust import apply_minute_adjustment

    repo, _ = setup
    factors = pl.DataFrame([event(factor=2.0), {"symbol": A, "trade_date": DAYS[2], "ex_factor": 3.0}])
    folder = repo.store.data_dir / "adj_factor"
    folder.mkdir(exist_ok=True)
    factors.write_parquet(folder / "all.parquet")
    daily = bars(A, [60.0, 30.0, 10.0])
    minute = daily.rename({"date": "datetime"}).with_columns(pl.col("datetime").cast(pl.Datetime))
    adjusted = _apply_adj_factor(daily, factors)
    intraday = apply_minute_adjustment(minute, repo.store.data_dir)
    assert adjusted["close"].to_list() == pytest.approx([10.0, 10.0, 10.0])
    assert intraday["close"].to_list() == pytest.approx(adjusted["close"].to_list())
    assert intraday["amount"][0] / (intraday["volume"][0] * 100) == pytest.approx(10.0)


@pytest.mark.parametrize("asset_type", ["stock", "etf"])
def test_daily_api_fallback_uses_factor_route_without_tickflow_permission(setup, monkeypatch, asset_type):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import kline

    repo, source = setup
    app = FastAPI()
    app.include_router(kline.router)
    app.state.repo = repo
    app.state.capabilities = SimpleNamespace(has=lambda cap: False)
    monkeypatch.setattr(repo, "resolve_asset_type", lambda symbol: asset_type)
    monkeypatch.setattr(repo, "get_daily_asset", lambda *a: pl.DataFrame())
    monkeypatch.setattr(kline, "_get_stock_info", lambda *a: {})
    monkeypatch.setattr(kline, "_get_asset_info", lambda *a: {})
    monkeypatch.setattr(kline, "_maybe_inject_live_candle", lambda request, symbol, rows, asset: rows)
    monkeypatch.setattr(kline_sync, "fetch_daily_for_chart", lambda *a: bars(A, [10.0, 5.0, 5.2]))
    monkeypatch.setattr(audit.custom, "provider_has_dataset", lambda *a: True)
    monkeypatch.setattr(kline_sync, "get_client", lambda: pytest.fail("must not call TickFlow"))
    source.get_adj_factors = lambda *a, **k: pl.DataFrame([event()])
    response = TestClient(app).get("/api/kline/daily", params={
        "symbol": A, "start_date": "2026-01-01", "end_date": "2026-03-03",
    })
    assert response.status_code == 200
    assert [row["close"] for row in response.json()["rows"]] == pytest.approx([5.0, 5.0, 5.2])
    source.get_adj_factors = lambda *a, **k: (_ for _ in ()).throw(ProviderRequestError("failed"))
    assert TestClient(app).get("/api/kline/daily", params={"symbol": A}).status_code == 502


def test_manual_etf_history_sync_loads_factors_before_enriched(setup, monkeypatch):
    from app.services import index_sync

    repo, source = setup
    repo.save_etf_instruments(pl.DataFrame({"symbol": [A], "name": ["ETF"]}))
    source.get_adj_factors = lambda *a, **k: pl.DataFrame([event()])
    source.get_daily = lambda *a, **k: bars(A, [10.0, 5.0, 5.2])
    monkeypatch.setattr(audit.custom, "provider_has_dataset", lambda *a: True)
    monkeypatch.setattr(audit.preferences, "get_daily_data_provider", lambda: "quantdash_user")
    count = index_sync.sync_daily_assets([A], repo, SimpleNamespace(has=lambda cap: False), days=730)
    assert count == 3
    assert stored(repo, "etf")["close"].to_list() == pytest.approx([5.0, 5.0, 5.2])
    assert not list((repo.store.data_dir / "kline_daily").glob("date=*/*.parquet"))


def test_universe_classifies_legacy_etf_minutes_by_dimensions(setup):
    repo, _ = setup
    seed(repo)
    etf = "588000.SH"
    repo.save_etf_instruments(pl.DataFrame({"symbol": [etf], "name": ["ETF"]}))
    part = repo.store.data_dir / "kline_minute" / "date=2025-01-02"
    part.mkdir()
    pl.DataFrame({"symbol": [etf], "datetime": [datetime(2025, 1, 2, 9, 31)]}).write_parquet(part / "part.parquet")
    assert etf not in {r["symbol"] for r in audit.collect_universe(repo.store.data_dir, "stock")}
    got = audit.collect_universe(repo.store.data_dir, "etf")
    assert got == [{"symbol": etf, "dimension": True, "minute_start": "2025-01-02",
                    "minute_end": "2025-01-02", "minute_rows": 1,
                    "history_start": "2025-01-02", "exchange": "SH"}]


def test_new_mixed_minute_batch_persists_etf_separately(setup, monkeypatch):
    from app.services import minute_adjust

    repo, _ = setup
    etf = "588000.SH"
    repo.save_etf_instruments(pl.DataFrame({"symbol": [etf], "name": ["ETF"]}))
    minute_adjust.mark_minute_basis_raw(repo.store.data_dir)
    frame = pl.concat([bars(A, [10.0] * 3), bars(etf, [1.0] * 3)]).rename({"date": "datetime"}).with_columns(pl.col("datetime").cast(pl.Datetime))
    monkeypatch.setattr(kline_sync, "_selected_minute_provider_name", lambda: "quantdash_user")
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda name: (object(), False, None))
    monkeypatch.setattr(kline_sync, "resolve_limit", lambda *a, **k: SimpleNamespace(batch=100, rpm=30))

    def fetch(symbols, **kwargs):
        kwargs["on_segment"](frame)
        return frame

    monkeypatch.setattr(kline_sync, "sync_minute_batch", fetch)
    assert kline_sync.sync_and_persist_minute([A, etf], repo, SimpleNamespace(has=lambda cap: True)) == 6
    stocks = pl.read_parquet(sorted((repo.store.data_dir / "kline_minute").glob("date=*/*.parquet")))
    etfs = pl.read_parquet(sorted((repo.store.data_dir / "kline_etf_minute").glob("date=*/*.parquet")))
    assert set(stocks["symbol"]) == {A}
    assert set(etfs["symbol"]) == {etf}
