"""Build only the verified backend source + official manifest, never databases."""

from pathlib import Path
import zipfile


def build(destination=None):
    root = Path(__file__).resolve().parent
    target = (
        Path(destination) if destination else root / "dist" / "lan-ma55-state-0.2.1.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    names = [
        "__init__.py",
        "engine.py",
        "pipeline.py",
        "store.py",
        "service.py",
        "extension.py",
    ]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(root / "extension.yaml", "extension.yaml")
        for name in names:
            archive.write(root / "backend" / name, "backend/" + name)
    return target


if __name__ == "__main__":
    print(build())
