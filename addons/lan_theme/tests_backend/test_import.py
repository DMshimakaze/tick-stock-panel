from copy import deepcopy
import json
from pathlib import Path

import pytest

from addons.lan_theme.backend.importer import FileSource, import_source, decode_document
from addons.lan_theme.backend.normalize import normalize_info
from addons.lan_theme.backend.schema import InvalidSnapshot, timestamp
from addons.lan_theme.backend.store import ImportLease, SnapshotStore

ASOF = "2026-11-09 03:00:00"
KNOWN = ["600519.SH", "300863.SZ", "000001.SZ"]


def save_file(tmp_path, raw, name="theme.json"):
    path = tmp_path / name
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def load(store, path, **kwargs):
    return import_source(
        store,
        FileSource(path),
        asof=kwargs.pop("asof", ASOF),
        source="Authorized local test fixture",
        known_symbols=KNOWN,
        **kwargs,
    )


@pytest.mark.parametrize("class_layer", ["2", "3"])
def test_three_layer_structure_direct_stocks_top_level_and_exact_tags(
    raw_theme, class_layer
):
    raw_theme["ClassLayer"] = class_layer
    theme = normalize_info(raw_theme, asof=ASOF, known_symbols=KNOWN)
    assert [n["depth"] for n in theme["nodes"]] == [1, 2]
    assert theme["nodes"][1]["path"] == ["供应商", "整机"]
    members = theme["memberships"]
    assert len(members) == 4
    child = next(m for m in members if m["symbol"] == "300863.SZ")
    assert child["source_fields"] == ["Level2.Stocks", "StockList.Tag"]
    assert next(m for m in members if m["symbol"] == "000001.SZ")["branch_path"] == []
    assert sum(m["symbol"] == "600519.SH" for m in members) == 2


def test_duplicate_names_ids_branches_and_ambiguous_bare_symbol(raw_theme):
    other = deepcopy(raw_theme["Table"][0])
    other["Level1"]["ID"] = "other-parent"
    other["Level2"][0]["ID"] = "other-child"
    raw_theme["Table"].append(other)
    theme = normalize_info(raw_theme, asof=ASOF, known_symbols=KNOWN + ["000001.SH"])
    assert len(theme["nodes"]) == 4
    assert len({n["key"] for n in theme["nodes"]}) == 4
    assert sum(m["symbol"] == "300863.SZ" for m in theme["memberships"]) == 2
    unknown = next(m for m in theme["memberships"] if m["StockID"] == "000001")
    assert unknown["symbol"] is None and unknown["symbol_status"] == "UNRESOLVED_SYMBOL"


@pytest.mark.parametrize(
    "field,value",
    [
        ("errcode", "1"),
        ("ID", None),
        ("Name", ""),
        ("Table", {}),
        ("StockList", None),
        ("UpdateTime", "bad-time"),
    ],
)
def test_source_errors_and_malformed_collections_are_rejected(raw_theme, field, value):
    raw_theme[field] = value
    with pytest.raises(InvalidSnapshot):
        normalize_info(raw_theme, asof=ASOF)


def test_invalid_nested_stock_tag_and_empty_theme(raw_theme):
    raw_theme["StockList"][0]["Tag"] = "not-an-array"
    with pytest.raises(InvalidSnapshot, match="Tag"):
        normalize_info(raw_theme, asof=ASOF)
    with pytest.raises(InvalidSnapshot, match="EMPTY_THEME"):
        normalize_info(
            dict(ID="empty", Name="DEMO", errcode=0, Table=[], Stocks=[]), asof=ASOF
        )


def test_update_idempotency_rejection_stale_and_rollback(tmp_path, raw_theme):
    store = SnapshotStore(tmp_path / "store")
    path = save_file(tmp_path, raw_theme)
    first = load(store, path)
    assert first["result"] == "IMPORTED"
    assert load(store, path)["result"] == "UNCHANGED"
    assert len(store.list_snapshots()) == 1
    changed = deepcopy(raw_theme)
    changed["BriefIntro"] = "DEMO updated provenance"
    second = load(store, save_file(tmp_path, changed))
    assert second["snapshot_id"] != first["snapshot_id"]
    bad = deepcopy(changed)
    bad["Table"] = []
    with pytest.raises(InvalidSnapshot, match="STRUCTURE_DROP"):
        load(store, save_file(tmp_path, bad))
    assert store.read()["snapshot"]["snapshot_id"] == second["snapshot_id"]
    assert store.read()["last_error"] is not None
    store.rollback(first["snapshot_id"])
    assert (
        SnapshotStore(store.folder).read()["snapshot"]["snapshot_id"]
        == first["snapshot_id"]
    )
    assert store.read()["last_error"] is None


def test_manifest_missing_rejected_and_complete_only_for_declared_scope(
    tmp_path, raw_theme
):
    store = SnapshotStore(tmp_path / "store")
    path = save_file(tmp_path, raw_theme)
    load(store, path)
    manifest = dict(
        format="lan-theme-manifest",
        format_version=1,
        files=[path.name],
        expected_theme_ids=["T1", "T2"],
        scope="PARTIAL",
    )
    file = save_file(tmp_path, manifest, "manifest.json")
    with pytest.raises(InvalidSnapshot):
        import_source(
            store, FileSource(path, file), asof=ASOF, source="Authorized catalog"
        )
    assert store.read()["last_error"]["missing"] == 1
    assert store.read()["snapshot"]["catalog_coverage"] == "UNKNOWN"
    manifest["expected_theme_ids"] = ["T1"]
    save_file(tmp_path, manifest, "manifest.json")
    import_source(store, FileSource(path, file), asof=ASOF, source="Authorized catalog")
    assert store.read()["snapshot"]["catalog_coverage"] == "MANIFEST_COMPLETE"


def test_partial_invalid_directory_never_replaces_valid_snapshot(tmp_path, raw_theme):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    store = SnapshotStore(tmp_path / "store")
    path = save_file(inputs, raw_theme)
    first = load(store, path)
    save_file(inputs, dict(errcode="401"), "failed.json")
    with pytest.raises(InvalidSnapshot):
        load(store, inputs)
    assert store.read()["snapshot"]["snapshot_id"] == first["snapshot_id"]
    assert store.read()["last_error"]["fetched"] == 2
    assert store.read()["last_error"]["valid"] == 1
    assert store.read()["last_error"]["rejected"] == 1


def test_versioned_json_jsonl_csv_exports_and_source_kind():
    row = dict(
        theme_id="T",
        theme_name="DEMO",
        branch_path=["服务", "整机"],
        branch_ids=["1", "2"],
        stock_code="300863.SZ",
    )
    header = dict(format="lan-theme-export", format_version=1, rows=[row])
    for name, body, version in [
        ("x.json", json.dumps(header).encode(), None),
        (
            "x.jsonl",
            (
                json.dumps({k: v for k, v in header.items() if k != "rows"})
                + "\n"
                + json.dumps(row)
            ).encode(),
            None,
        ),
        (
            "x.csv",
            "theme_id,theme_name,branch_path,stock_code\nT,DEMO,服务 / 整机,300863.SZ\n".encode(),
            1,
        ),
    ]:
        themes = decode_document(
            name, body, asof=ASOF, known_symbols=KNOWN, export_version=version
        )
        assert themes[0]["source_kind"] == "AUTHORIZED_EXPORT"
        assert themes[0]["memberships"][0]["branch_path"] == ["服务", "整机"]
    with pytest.raises(InvalidSnapshot, match="CSV_REQUIRES"):
        decode_document(
            "x.csv", b"", asof=ASOF, known_symbols=KNOWN, export_version=None
        )


def test_five_snapshot_retention_lease_demo_and_time(tmp_path, raw_theme):
    store = SnapshotStore(tmp_path / "store")
    path = save_file(tmp_path, raw_theme)
    with pytest.raises(InvalidSnapshot, match="DEMO_NOT_ALLOWED"):
        load(store, path, demo=True)
    for i in range(6):
        raw_theme["BriefIntro"] = "DEMO " + str(i)
        load(store, save_file(tmp_path, raw_theme))
    assert len(store.list_snapshots()) == 5
    lease = ImportLease(store.folder / "import.lock")
    try:
        with pytest.raises(InvalidSnapshot, match="IMPORT_BUSY"):
            store.rollback(store.list_snapshots()[0]["snapshot_id"])
    finally:
        lease.close()
    assert timestamp("2026-11-08T19:00:00+00:00") == ASOF
    assert timestamp("2026-11-09") == "2026-11-09 00:00:00"
    assert not list(Path(store.folder).glob("*kline*"))


def test_empty_manifest_and_truncated_input_update_rejection(
    tmp_path, raw_theme, capsys
):
    from addons.lan_theme.import_snapshot import main

    path = save_file(tmp_path, raw_theme)
    base = [
        "--data-dir",
        str(tmp_path / "data"),
        "import",
        "--input",
        str(path),
        "--asof",
        ASOF,
        "--source",
        "Authorized test",
    ]
    assert main(base) == 0
    path.write_text('{"errcode":', encoding="utf-8")
    assert main(base) == 2
    manifest = save_file(tmp_path, {}, "manifest.json")
    assert main(base + ["--manifest", str(manifest)]) == 2
    store = SnapshotStore(tmp_path / "data/user_data/lan_theme")
    assert store.read()["last_error"]["error"] == "MANIFEST_VERSION_UNSUPPORTED"
    assert store.read()["snapshot"] is not None
    capsys.readouterr()


def test_catalog_count_collapse_cannot_silently_replace_snapshot(tmp_path, raw_theme):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    store = SnapshotStore(tmp_path / "store")
    for n in range(3):
        raw_theme["ID"] = str(n)
        save_file(inputs, raw_theme, f"{n}.json")
    first = load(store, inputs)
    with pytest.raises(InvalidSnapshot, match="STRUCTURE_DROP"):
        load(store, inputs / "0.json")
    assert store.read()["snapshot"]["snapshot_id"] == first["snapshot_id"]
