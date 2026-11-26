"""Build the unified official package; include source and built page only."""

from argparse import ArgumentParser
from pathlib import Path
import zipfile


def build(destination=None, page=None):
    root = Path(__file__).resolve().parent
    target = (
        Path(destination) if destination else root / "dist" / "lan-ma55-0.13.4.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    names = ['__init__.py','engine.py','pipeline.py','store.py','extension.py',
             'runtime.py','runtime_status.py','result_publisher.py','shared_store.py','worker_lease.py',
             'unified_store.py','market_store.py','market_grid.py','collector.py','request_budget.py',
             'indicator_cache.py','native15_shadow.py','native15_store.py',
             'native15_signal_adapter.py','native15_scheduler.py','native15_owner.py',
             'focus_pool.py','dc_taxonomy.py','dc_store.py','dc_transport.py','dc_maintenance.py',
             'dc_focus_owner.py','ma55_levels.py','exchange_sessions.py','multiframe_input.py',
             'daily_store.py','observations.py','workbench_bridge.py',
             'multiframe_core/__init__.py','multiframe_core/features.py',
             'multiframe_core/structure.py','multiframe_core/multiframe.py']
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(root / "extension.yaml", "extension.yaml")
        for name in names:
            archive.write(root / "backend" / name, "backend/" + name)
        archive.write(Path(page) if page else root / "dist" / "extension.js", "frontend/extension.mjs")
    return target


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--page", type=Path)
    args = parser.parse_args()
    print(build(args.destination, args.page))
