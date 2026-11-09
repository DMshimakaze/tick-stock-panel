"""Only the package implementation and compiled page enter the official ZIP."""

from pathlib import Path
import zipfile


def build(destination=None):
    root = Path(__file__).resolve().parent
    target = Path(destination) if destination else root / "dist" / "lan-theme-1.0.2.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(root / "extension.yaml", "extension.yaml")
        for name in (
            "__init__.py",
            "schema.py",
            "normalize.py",
            "store.py",
            "importer.py",
            "publisher.py",
            "extension.py",
            "cli.py",
        ):
            archive.write(root / "backend" / name, "backend/" + name)
        archive.write(root / "dist" / "extension.js", "frontend/extension.mjs")
    return target


if __name__ == "__main__":
    print(build())
