import sqlite3
import tempfile
import unittest
from pathlib import Path

from addons.lan_ma55.backend.focus_pool import FocusPool, ScopeConflict, decode_catalog, decode_members

A, B = "industry:881001.TI", "cn_concept:886001.TI"
X, Y, Z = "600001.SH", "000001.SZ", "830001.BJ"


def response(items, at=100):
    return {"code": 0, "data": {"timestamp": at, "item": items}}


def snap(key, symbols, at=100):
    return decode_members(key, "test", response([{"thscode": s} for s in symbols], at), at+1)


class TestFocusPool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.pool = FocusPool(self.path)

    def apply(self, keys, snapshots=()):
        return self.pool.apply(keys, snapshots, expected_revision=self.pool.read()["revision"])

    def test_catalog_identity(self):
        r = decode_catalog("industry", response([{"thscode": "881001.TI", "name": "同名"},
                                                {"thscode": "881002.TI", "name": "同名"}]))
        self.assertEqual(len(r["entries"]), 2)
        with self.assertRaises(ValueError):
            decode_catalog("industry", response([{"thscode": "881001.TI", "name": "甲"},
                                                {"thscode": "881001.TI", "name": "乙"}]))

    def test_union_dedup_and_overlap_removal(self):
        r = self.apply([A, B], [snap(A, [X, X, Y]), snap(B, [Y, Z])])
        self.assertEqual(r["added"], sorted([X, Y, Z]))
        self.assertEqual(r["members"][Y], sorted([A, B]))
        r = self.apply([B])
        self.assertEqual(r["removed"], [X])
        self.assertEqual(r["retained"], sorted([Y, Z]))

    def test_unchanged_and_timestamp_only(self):
        r = self.apply([A], [snap(A, [X])])
        self.assertEqual(self.apply([A])["revision"], r["revision"])
        new = self.apply([A], [snap(A, [X], 200)])
        self.assertFalse(new["membership_changed"])
        self.assertEqual(new["added"], [])
        self.assertEqual(new["removed"], [])

    def test_membership_changes_without_selection_change(self):
        self.apply([A], [snap(A, [X, Y])])
        r = self.apply([A], [snap(A, [Y, Z], 200)])
        self.assertEqual((r["added"], r["removed"], r["retained"]), ([Z], [X], [Y]))

    def test_failed_new_selection_is_atomic(self):
        self.apply([A], [snap(A, [X])])
        before = self.pool.read()
        with self.assertRaises(ValueError):
            self.apply([B])
        self.assertEqual(self.pool.read(), before)

    def test_invalid_and_empty_source(self):
        for payload in ({"code": 3002, "data": None}, response([]), response([{"thscode": "600001"}])):
            with self.assertRaises(ValueError):
                decode_members(A, "test", payload, 101)

    def test_stale_refresh_and_concurrent_edit(self):
        self.apply([A], [snap(A, [X], 200)])
        before = self.pool.read()
        with self.assertRaises(ValueError):
            self.apply([A], [snap(A, [Y], 100)])
        self.assertEqual(self.pool.read(), before)
        self.apply([])
        with self.assertRaises(ScopeConflict):
            self.pool.apply([A], [snap(A, [X], 300)], expected_revision=before["revision"])
        self.assertEqual(self.pool.read()["members"], {})

    def test_persistence_and_no_cache_deletion(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE existing_bars(symbol TEXT)")
            conn.execute("INSERT INTO existing_bars VALUES (?)", (X,))
        self.apply([A], [snap(A, [X])])
        self.assertEqual(FocusPool(self.path).read()["members"], {X: [A]})
        self.apply([])
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT symbol FROM existing_bars").fetchall(), [(X,)])


if __name__ == "__main__":
    unittest.main()
