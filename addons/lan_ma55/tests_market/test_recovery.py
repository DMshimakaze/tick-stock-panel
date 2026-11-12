from addons.lan_ma55.backend.market_grid import LEGACY, aggregate
from .test_rolling import sample, opened, put


def test_source_switch_never_splices_legacy_into_tickflow(tmp_path):
    sessions, rows = sample(250)
    store = opened(tmp_path, sessions)
    try:
        legacy = aggregate(rows, sessions, max(rows), source=LEGACY)["15m"]
        store.conn.execute("DELETE FROM instrument_state")
        store.conn.commit()
        store.import_legacy(
            dict(symbol="600000.SH", eligible=True, raw_tick=0.01),
            legacy,
            [],
            max(rows),
        )
        assert not store.read("600000.SH", "15m", max(rows))["analysis_allowed"]
        tail = {t: rows[t] for t in list(rows)[-3:]}
        put(store, tail, sessions)
        out = store.read("600000.SH", "15m", max(rows))
        assert out["row_count"] == 250 and out["source_id"] == LEGACY
        assert all(b["source_id"] == LEGACY for b in out["bars"])
    finally:
        store.close()


def test_overlap_verified_or_individual_discrepancy(tmp_path):
    sessions, rows = sample(60)
    store = opened(tmp_path, sessions)
    try:
        legacy = aggregate(rows, sessions, max(rows), source=LEGACY)["15m"]
        store.conn.execute("DELETE FROM instrument_state")
        store.conn.commit()
        store.import_legacy(
            dict(symbol="600000.SH", eligible=True, raw_tick=0.01),
            legacy,
            [],
            max(rows),
        )
        put(store, rows, sessions)
        assert store.instrument("600000.SH")["transition"] == "OVERLAP_VERIFIED"
    finally:
        store.close()
