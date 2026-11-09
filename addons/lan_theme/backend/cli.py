"""Installed-package entry: run `python -m backend.cli` from the version folder."""

import argparse
import getpass
import json
from pathlib import Path
import sys

import httpx

from .importer import FileSource, import_source
from .publisher import Publisher
from .schema import InvalidSnapshot, SYMBOL, MAX_INPUT_BYTES
from .store import SnapshotStore


def load_json(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise InvalidSnapshot("INPUT_EXCEEDS_32_MIB")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def init_views(base, password_stdin=False):
    # The administrator session initializes views once, never a service credential.
    with httpx.Client(base_url=base, timeout=40, trust_env=False) as client:
        response = client.get("/api/auth/status")
        response.raise_for_status()
        if response.json()["configured"]:
            password = (
                sys.stdin.readline().rstrip("\r\n")
                if password_stdin
                else getpass.getpass("TSP访问密码（不保存）: ")
            )
            response = client.post("/api/auth/login", json=dict(password=password))
            response.raise_for_status()
        publisher = Publisher(base, session=client.cookies.get("tf_session"))
        try:
            publisher.prepare()
        finally:
            publisher.close()
    return dict(
        result="VIEWS_INITIALIZED", tables=["lan_theme_tree", "lan_theme_status"]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import authorized KPL snapshots; no KPL/market requests"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Absolute host DATA_DIR (never the package version directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("import")
    load.add_argument("--input", type=Path, required=True)
    load.add_argument("--manifest", type=Path)
    load.add_argument("--asof", required=True, help="Beijing snapshot time")
    load.add_argument("--source", required=True)
    load.add_argument(
        "--provenance",
        required=True,
        help="Verifiable authorization/export rights description",
    )
    load.add_argument(
        "--identity-map",
        type=Path,
        help="Versioned full security master or explicit administrator mapping",
    )
    load.add_argument(
        "--symbols-file",
        type=Path,
        help="Legacy symbol array; does NOT resolve bare codes",
    )
    load.add_argument("--export-version", type=int, choices=[1])
    load.add_argument("--demo", action="store_true")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("snapshot_id")
    sub.add_parser("list")
    initialize = sub.add_parser("init-views")
    initialize.add_argument("--api-base", required=True)
    initialize.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "init-views" and (
        args.data_dir is None or not args.data_dir.is_absolute()
    ):
        parser.error(
            "import/list/rollback require --data-dir with the absolute host DATA_DIR"
        )
    if args.command == "init-views":
        try:
            print(
                json.dumps(
                    init_views(args.api_base, args.password_stdin), ensure_ascii=False
                )
            )
            return 0
        except (httpx.HTTPError, InvalidSnapshot) as exc:
            code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            print(
                json.dumps(
                    dict(
                        result="REJECTED",
                        error="VIEW_INITIALIZATION_FAILED",
                        http_status=code,
                        action="Check the local API address and administrator login; Tokens cannot create configurations.",
                    )
                )
            )
            return 2
    folder = args.data_dir / "user_data" / "lan_theme"
    try:
        store = SnapshotStore(folder)
    except (InvalidSnapshot, OSError) as exc:
        print(
            json.dumps(
                dict(
                    result="REJECTED",
                    error=str(exc),
                    action="Check the data directory; wait for the current import lease to finish.",
                ),
                ensure_ascii=False,
            )
        )
        return 2
    before_revision = store.read()["revision"]
    try:
        if args.command == "import":
            settings_path = folder / "settings.json"
            settings = load_json(settings_path) if settings_path.exists() else {}
            symbols = load_json(args.symbols_file) if args.symbols_file else []
            if not isinstance(symbols, list) or any(
                not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in symbols
            ):
                raise InvalidSnapshot("SYMBOLS_FILE_REQUIRES_CANONICAL_SYMBOL_ARRAY")
            mapping = load_json(args.identity_map) if args.identity_map else None
            result = import_source(
                store,
                FileSource(args.input, args.manifest),
                asof=args.asof,
                source=args.source,
                provenance=args.provenance,
                known_symbols=symbols,
                identity_map=mapping,
                export_version=args.export_version,
                demo=args.demo,
                demo_allowed=settings.get("allow_demo") is True,
            )
            result["counts"] = store.read()["snapshot"]["counts"]
        elif args.command == "rollback":
            result = store.rollback(args.snapshot_id)
        else:
            result = store.list_snapshots()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InvalidSnapshot, ValueError, OSError) as exc:
        if args.command == "import" and store.read()["revision"] == before_revision:
            store.reject(
                dict(
                    result="REJECTED",
                    error=str(exc)
                    if isinstance(exc, InvalidSnapshot)
                    else "INVALID_LOCAL_INPUT",
                )
            )
        print(
            json.dumps(
                dict(
                    result="REJECTED",
                    error=store.read()["last_error"]
                    if args.command == "import"
                    else str(exc),
                    action="Correct the reported file/schema/date/provenance, then retry. The active valid snapshot is retained; use list/rollback for retained versions.",
                ),
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
