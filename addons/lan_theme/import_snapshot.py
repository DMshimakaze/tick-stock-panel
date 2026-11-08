"""Explicit local import/rollback CLI; never invokes a KPL or market endpoint."""

import argparse
import json
from pathlib import Path

from .backend.importer import FileSource, import_source
from .backend.schema import InvalidSnapshot, SYMBOL
from .backend.store import SnapshotStore


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import authorized KPL snapshots without online requests"
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("import")
    load.add_argument("--input", type=Path, required=True)
    load.add_argument("--manifest", type=Path)
    load.add_argument("--asof", required=True, help="Beijing YYYY-MM-DD HH:MM:SS")
    load.add_argument(
        "--source", required=True, help="Authorized source/export provenance label"
    )
    load.add_argument(
        "--symbols-file", type=Path, help="JSON list of known canonical TSP symbols"
    )
    load.add_argument("--export-version", type=int, choices=[1])
    load.add_argument("--demo", action="store_true")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("snapshot_id")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    folder = args.data_dir / "user_data" / "lan_theme"
    store = SnapshotStore(folder)
    before_revision = store.read()["revision"]
    try:
        if args.command == "import":
            settings_path = folder / "settings.json"
            settings = (
                json.loads(settings_path.read_text(encoding="utf-8"))
                if settings_path.exists()
                else {}
            )
            symbols = (
                json.loads(args.symbols_file.read_text(encoding="utf-8"))
                if args.symbols_file
                else []
            )
            if not isinstance(symbols, list) or any(
                not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in symbols
            ):
                raise InvalidSnapshot("SYMBOLS_FILE_REQUIRES_CANONICAL_SYMBOL_ARRAY")
            result = import_source(
                store,
                FileSource(args.input, args.manifest),
                asof=args.asof,
                source=args.source,
                known_symbols=symbols,
                export_version=args.export_version,
                demo=args.demo,
                demo_allowed=settings.get("allow_demo") is True,
            )
        elif args.command == "rollback":
            result = store.rollback(args.snapshot_id)
        else:
            result = store.list_snapshots()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InvalidSnapshot, ValueError, OSError) as exc:
        # Import failure details live in bounded store metadata, not raw responses.
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
                ),
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
