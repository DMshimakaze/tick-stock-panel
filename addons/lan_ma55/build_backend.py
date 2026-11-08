"""Build the unified official package; include source and built page only."""

from pathlib import Path
import zipfile


def build(destination=None):
    root = Path(__file__).resolve().parent
    target = (
        Path(destination) if destination else root / "dist" / "lan-ma55-0.3.2.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    names = ['__init__.py','engine.py','pipeline.py','store.py','extension.py','market.py',
             'live_store.py','runtime.py','result_publisher.py','shared_store.py','market_client.py','worker_lease.py']
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(root / "extension.yaml", "extension.yaml")
        for name in names:
            archive.write(root / "backend" / name, "backend/" + name)
        archive.write(root / "dist" / "extension.js", "frontend/extension.mjs")
    return target


if __name__ == "__main__":
    print(build())
