import json
import os
import sqlite3
import subprocess
import sys
import zipfile

import pytest

from addons.lan_theme.backend.importer import FileSource, import_source
from addons.lan_theme.backend.normalize import normalize_info
from addons.lan_theme.backend.schema import InvalidSnapshot
from addons.lan_theme.backend.store import SnapshotStore
from addons.lan_theme.backend.store import ImportLease
from addons.lan_theme.build_backend import build

ASOF = "2026-11-09 00:00:00"


def mapping(**kwargs):
    return dict(
        format="lan-theme-identity-map",
        format_version=1,
        source="DEMO administrator evidence",
        asof_cn=ASOF,
        **kwargs,
    )


def test_bare_code_requires_evidence_and_master_ambiguity_stays_null(raw_theme):
    raw_theme["Table"] = []
    raw_theme["StockList"] = []
    raw_theme["Stocks"] = [dict(StockID="000001", prod_name="DEMO")]
    unresolved = normalize_info(raw_theme, asof=ASOF, known_symbols=["000001.SZ"])
    assert unresolved["memberships"][0]["symbol"] is None
    master = mapping(
        kind="AUTHORITATIVE_UNIVERSE", complete=True, symbols=["000001.SH", "000001.SZ"]
    )
    assert (
        normalize_info(raw_theme, asof=ASOF, identity_map=master)["memberships"][0][
            "symbol"
        ]
        is None
    )
    admin = mapping(kind="ADMIN_EXPLICIT_MAP", mapping={"000001": "000001.SZ"})
    member = normalize_info(raw_theme, asof=ASOF, identity_map=admin)["memberships"][0]
    assert member["symbol"] == "000001.SZ"
    assert member["symbol_resolution"]["kind"] == "ADMIN_EXPLICIT_MAP"
    assert member["symbol_resolution"]["source"] == admin["source"]
    master["complete"] = False
    with pytest.raises(InvalidSnapshot, match="COMPLETE_SECURITY_MASTER_REQUIRED"):
        normalize_info(raw_theme, asof=ASOF, identity_map=master)


def test_provenance_and_atomic_transaction_rollback(tmp_path, raw_theme):
    store = SnapshotStore(tmp_path / "data")
    path = tmp_path / "source.json"
    path.write_text(json.dumps(raw_theme), encoding="utf-8")
    with pytest.raises(InvalidSnapshot, match="license/provenance"):
        import_source(store, FileSource(path), asof=ASOF, source="DEMO")
    import_source(
        store,
        FileSource(path),
        asof=ASOF,
        source="DEMO",
        provenance="Synthetic test-only structure",
    )
    active = store.read()["snapshot"]["snapshot_id"]
    # A writer interrupted before commit cannot change the active snapshot.
    with pytest.raises(RuntimeError):
        with store.write() as db:
            store.set_meta(db, "active", "uncommitted")
            raise RuntimeError("simulated interrupted import")
    assert store.read()["snapshot"]["snapshot_id"] == active
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_zip_only_cli_import_reject_rollback_without_checkout(tmp_path, raw_theme):
    archive = build(tmp_path / "lan-theme.zip")
    installed = tmp_path / "installed-version"
    with zipfile.ZipFile(archive) as bundle:
        assert "backend/cli.py" in bundle.namelist()
        assert b"1.0.3" in bundle.read("extension.yaml")
        bundle.extractall(installed)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(raw_theme), encoding="utf-8")
    data = tmp_path / "isolated-data"
    folder = data / "user_data/lan_theme"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text('{"allow_demo":true}', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    def run(*args, code=0):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                "-m",
                "backend.cli",
                "--data-dir",
                str(data),
                *args,
            ],
            cwd=installed,
            env=env,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        assert result.returncode == code, result.stdout + result.stderr
        return json.loads(result.stdout)

    args = [
        "import",
        "--input",
        str(source),
        "--asof",
        ASOF,
        "--source",
        "DEMO ZIP only",
        "--provenance",
        "Synthetic fixture, not authorized commercial data",
        "--demo",
    ]
    first = run(*args)
    assert run(*args)["result"] == "UNCHANGED"
    source.write_text('{"errcode":', encoding="utf-8")
    assert run(*args, code=2)["result"] == "REJECTED"
    assert run("rollback", first["snapshot_id"])["result"] == "ROLLED_BACK"
    assert run("list")[0]["snapshot_id"] == first["snapshot_id"]
    missing = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "backend.cli", "list"],
        cwd=installed,
        env=env,
        text=True,
        capture_output=True,
    )
    assert missing.returncode == 2 and "absolute host DATA_DIR" in missing.stderr
    assert not (installed / "data").exists()


def test_node_id_conflict_and_manifest_path_retain_snapshot(tmp_path, raw_theme):
    from copy import deepcopy

    store = SnapshotStore(tmp_path / "data")
    path = tmp_path / "input.json"
    path.write_text(json.dumps(raw_theme), encoding="utf-8")
    first = import_source(
        store,
        FileSource(path),
        asof=ASOF,
        source="DEMO",
        provenance="Synthetic structure only",
    )
    duplicate = deepcopy(raw_theme["Table"][0])
    duplicate["Level1"]["Name"] = "conflicting name"
    raw_theme["Table"].append(duplicate)
    path.write_text(json.dumps(raw_theme), encoding="utf-8")
    with pytest.raises(InvalidSnapshot, match="CONFLICTING_NODE_ID"):
        import_source(
            store,
            FileSource(path),
            asof=ASOF,
            source="DEMO",
            provenance="Synthetic structure only",
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            dict(
                format="lan-theme-manifest", format_version=1, files=["../outside.json"]
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidSnapshot, match="MANIFEST_PATH_OUTSIDE"):
        import_source(
            store,
            FileSource(path, manifest),
            asof=ASOF,
            source="DEMO",
            provenance="Synthetic structure only",
        )
    assert store.read()["snapshot"]["snapshot_id"] == first["snapshot_id"]


def test_cli_busy_rejects_without_traceback(tmp_path, capsys):
    from addons.lan_theme.backend.cli import main

    folder = tmp_path / "user_data/lan_theme"
    SnapshotStore(folder)
    lease = ImportLease(folder / "import.lock")
    try:
        assert main(["--data-dir", str(tmp_path), "list"]) == 2
        assert "IMPORT_BUSY" in capsys.readouterr().out
    finally:
        lease.close()
