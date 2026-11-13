"""Run the finite post-migration synthetic integration load and request replay."""

import argparse
import json
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend"))
    from addons.lan_ma55.tools.stress_db import run as database_load

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--symbols", type=int, default=5600)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    folder = Path(args.output)
    if args.native:
        from addons.lan_ma55.tools.stress_native15 import run as database_load
    database = database_load(folder, args.symbols)
    print(
        json.dumps(
            dict(
                db=database,
                request_pools=json.loads(
                    (folder / "request-metrics.json").read_text("utf-8")
                )["requests"],
            ),
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
