import json
import duckdb
from addons.lan_marketdata.import_legacy import migrate
from addons.lan_ma55.backend.market_store import Store
from addons.lan_ma55.backend.market_grid import LEGACY, aggregate
from .test_rolling import sample


def test_offline_import_readonly_idempotent_and_bounded(tmp_path):
    sessions, five = sample(251)
    source = tmp_path / "old.duckdb"
    conn = duckdb.connect(str(source))
    conn.execute("CREATE TABLE meta(key VARCHAR,payload VARCHAR)")
    conn.execute("CREATE TABLE instrument(symbol VARCHAR,payload VARCHAR)")
    conn.execute(
        "CREATE TABLE bars(symbol VARCHAR,timeframe VARCHAR,stamp VARCHAR,payload VARCHAR)"
    )
    for k, v in {"sessions": sessions, "universe": {"expected": 1}}.items():
        conn.execute("INSERT INTO meta VALUES(?,?)", [k, json.dumps(v)])
    info = dict(
        symbol="600000.SH",
        name="Synthetic",
        eligible=True,
        raw_tick=0.01,
        source_type="stock",
        events=[],
        factors_day=sessions[-1],
    )
    conn.execute(
        "INSERT INTO instrument VALUES(?,?)", [info["symbol"], json.dumps(info)]
    )
    bars = aggregate(five, sessions, max(five))["15m"]
    conn.executemany(
        "INSERT INTO bars VALUES(?,?,?,?)",
        [(info["symbol"], "15m", b["timestamp"], json.dumps(b)) for b in bars],
    )
    conn.close()
    before = source.stat().st_size
    target = tmp_path / "new.sqlite3"
    assert migrate(source, target)["bars15"] == 250
    assert migrate(source, target)["skipped"] == 1
    assert source.stat().st_size == before
    store = Store(target)
    try:
        out = store.read(info["symbol"], "15m", max(five))
        assert out["source_id"] == LEGACY and out["row_count"] == 250
        assert out["state"] == "REBUILD_PENDING" and not out["analysis_allowed"]
    finally:
        store.close()
