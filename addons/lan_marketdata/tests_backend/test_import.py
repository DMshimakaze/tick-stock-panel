"""Controlled legacy import checks; no production data is included."""

import json
import sqlite3

import pytest

from addons.lan_marketdata.backend.store import Store
from addons.lan_marketdata.import_legacy import ma55_import, kline_import


def test_unknown_ma55_price_provenance_preserves_source_and_destination(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE runtime_meta(key TEXT,payload TEXT)")
        conn.execute(
            "INSERT INTO runtime_meta VALUES(?,?)", ("data_mode", json.dumps("UNKNOWN"))
        )
    store = Store(tmp_path / "rolling.duckdb")
    try:
        with pytest.raises(ValueError, match="provenance"):
            ma55_import(store, path)
        assert not store.inventory()
    finally:
        store.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_meta").fetchone()[0] == 1


def test_legacy_raw_grid_is_checked_without_rewriting_source(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""CREATE TABLE runtime_meta(key TEXT,value TEXT);
            CREATE TABLE tracked_symbol(symbol TEXT,metadata TEXT,events TEXT);
            CREATE TABLE bar_5m(symbol TEXT,end_time TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,amount REAL,source TEXT,basis TEXT);
            CREATE TABLE bar_daily AS SELECT * FROM bar_5m WHERE 0;""")
        conn.execute(
            "INSERT INTO runtime_meta VALUES(?,?)",
            ("sessions", json.dumps(["2026-11-06"])),
        )
        conn.execute(
            "INSERT INTO tracked_symbol VALUES(?,?,?)",
            (
                "600000.SH",
                json.dumps(
                    dict(symbol="600000.SH", type="stock", ext=dict(tick_size=0.01))
                ),
                "[]",
            ),
        )
        for at in ("09:35:00", "09:37:00"):
            conn.execute(
                "INSERT INTO bar_5m VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "600000.SH",
                    "2026-11-06 " + at,
                    10,
                    12,
                    9,
                    11,
                    3,
                    3300,
                    "QuantDash",
                    "RAW",
                ),
            )
    store = Store(tmp_path / "rolling.duckdb")
    try:
        result = kline_import(store, path)
        assert result["rejected"] == 1
        assert (
            store.raw_rows("600000.SH", "5m")[0]["timestamp"] == "2026-11-06 09:35:00"
        )
        assert len(store.raw_rows("600000.SH", "5m")) == 1
    finally:
        store.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM bar_5m").fetchone()[0] == 2
