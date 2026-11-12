from contextlib import closing
from threading import Thread

import pytest
from addons.lan_ma55.backend.market_store import Store, reader
from .test_rolling import sample, opened, put


def test_restart_duplicate_and_single_writer(tmp_path):
    sessions, rows = sample(251)
    store = opened(tmp_path, sessions)
    put(store, rows, sessions)
    with pytest.raises(RuntimeError, match="writer"):
        Store(store.path)
    store.close()
    store = Store(tmp_path / "rolling15.sqlite3")
    try:
        put(store, rows, sessions)
        assert store.read("600000.SH", "15m", max(rows))["row_count"] == 250
    finally:
        store.close()


def test_readers_observe_previous_commit_during_writer_transaction(tmp_path):
    sessions, rows = sample(60)
    store = opened(tmp_path, sessions)
    put(store, rows, sessions)
    result = []
    try:
        store.conn.execute("BEGIN IMMEDIATE")
        store.conn.execute("DELETE FROM bar15")

        def read():
            with closing(reader(store.path)) as conn:
                result.append(conn.execute("SELECT count(*) FROM bar15").fetchone()[0])

        t = Thread(target=read)
        t.start()
        t.join(2)
        assert not t.is_alive() and result == [60]
        store.conn.rollback()
    finally:
        store.close()
