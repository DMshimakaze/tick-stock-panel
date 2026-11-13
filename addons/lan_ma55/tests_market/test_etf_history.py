from copy import deepcopy


from addons.lan_ma55.backend.runtime import classify
from addons.lan_ma55.backend.market_grid import wall, required_history, history_capacity
from addons.lan_ma55.backend.unified_store import UnifiedStore
from .test_rolling import sample


def test_authoritative_asset_types_tick_listing_and_exclusions():
    items = [dict(symbol=s, exchange=ex, type=t, region=region,
                  ext=dict(tick_size=tick, listing_date="2011-01-01"))
             for s,ex,t,region,tick in [
                 ("510050.SH","SH","etf","CN",0.001),
                 ("159915.SZ","SZ","etf","CN",0.001),
                 ("600000.SH","SH","stock","CN",0.01),
                 ("000001.SH","SH","index","CN",0.01),
                 ("000002.SH","SH","fund","CN",0.001),
                 ("123456.US","US","etf","US",0.01),
             ]]
    rows, stats = classify(items, "2026-11-12")
    assert stats["stock"] == 1 and stats["etf"] == 2 and stats["expected"] == 3
    assert [r["raw_tick"] for r in rows[:3]] == [0.001,0.001,0.01]
    assert not any(r["eligible"] for r in rows[3:])
    missing=deepcopy(items[0])
    missing["ext"].pop("tick_size")
    assert classify([missing],"2026-11-12")[1]["unpriced"] == 1
    assert required_history("60m") == 294 and history_capacity() >= 1176
    assert required_history("60m", 500) == 732


def test_etf_single_transaction_frozen_250_and_non_destructive_history(tmp_path, monkeypatch):
    sessions, five=sample(1284)
    symbols=["510050.SH","159915.SZ"]
    info, stats=classify([dict(symbol=s,type="etf",region="CN",ext=dict(tick_size=.001,listing_date="2000-01-01")) for s in symbols],sessions[-1])
    with UnifiedStore(tmp_path/"state.sqlite3") as owner:
        owner.refresh_master(info,stats,sessions)
        last=list(five)[-750:]
        for symbol in symbols:
            owner.accept_market(symbol,{t:five[t] for t in last},sessions=sessions,start=last[0],end=last[-1],now=last[-1],events=[],factor_response=True,live_after=last[-1])
        previous=[tuple(r) for r in owner.conn.execute("SELECT * FROM signal_event")]
        risk=[tuple(r) for r in owner.conn.execute("SELECT * FROM event_risk")]
        before=owner.market.instrument(symbols[0])["latest_completed15"]
        older={t:r for t,r in five.items() if t<last[0]}
        for symbol in symbols:
            owner.market.backfill(symbol,older,sessions=sessions,start=min(older),end=max(older),now=last[-1])
            assert owner.market.instrument(symbol)["latest_completed15"] == before
            assert owner.market.read(symbol,"60m",before)["ma_history"]["state"] == "READY"
            assert len(owner.market.read(symbol,"15m",before,strategy=True)["bars"]) == 250
        assert [tuple(r) for r in owner.conn.execute("SELECT * FROM signal_event")] == previous
        assert [tuple(r) for r in owner.conn.execute("SELECT * FROM event_risk")] == risk
        assert owner.market.history_candidates(before) == []
        assert len(owner.market.prices(before[:10])) == 2
        assert owner.market.prices("1999-01-01") == []


def test_refresh_counts_only_real_target_bars_in_current_stock_etf_generation(tmp_path, monkeypatch):
    sessions,five=sample(60)
    end=max(five)
    from addons.lan_ma55.backend import market_store
    monkeypatch.setattr(market_store,"now_cn",lambda: wall(end)+__import__('datetime').timedelta(minutes=2))
    with UnifiedStore(tmp_path/"state.sqlite3") as owner:
        rows, overview=classify([dict(symbol=s,type=t,region="CN",ext=dict(tick_size=tick)) for s,t,tick in
            [("600000.SH","stock",.01),("510050.SH","etf",.001),("159915.SZ","etf",.001)]],end[:10])
        owner.refresh_master(rows,overview,sessions)
        owner.market.accept("600000.SH",five,sessions=sessions,start=min(five),end=end,now=end,events=None,factor_response=True)
        owner.market.failed(["510050.SH"],end,end,"SOURCE_UNAVAILABLE")
        value=owner.market.snapshot()["refresh"]
        assert value["refreshed"]==1 and value["expected"]==3
        assert value["target_15m"]==value["latest_completed15"]==end
        assert value["updating"]
        assert value["generation"]==owner.market.meta("generation")


def test_missing_historical_bucket_cools_down_without_rewinding_live_watermark(tmp_path):
    from .test_rolling import opened, put
    sessions, five=sample(1280)
    times=list(five)
    missing=times[-600:-597]
    store=opened(tmp_path,sessions)
    try:
        put(store,{t:r for t,r in five.items() if t not in missing},sessions)
        before=store.instrument("600000.SH")["latest_completed15"]
        store.backfill("600000.SH",{},sessions=sessions,start=missing[0],end=missing[-1],now=max(five))
        assert store.instrument("600000.SH")["latest_completed15"]==before
        assert store.history_progress("600000.SH")["stop_reason"]=="GAP"
        assert store.history_progress("600000.SH")["next_retry"] > 0
    finally:
        store.close()
