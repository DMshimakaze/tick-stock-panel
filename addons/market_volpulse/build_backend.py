"""Package the standalone VolPulse page module and TSP backend extension."""
from argparse import ArgumentParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build(destination=None, page=None):
    root = Path(__file__).resolve().parent
    page = Path(page) if page else root / 'dist/extension.js'
    if not page.is_file():
        raise FileNotFoundError('Run pnpm build first')
    target = Path(destination) if destination else root / 'dist/market-volpulse-0.1.0.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    backend = sorted((root / 'backend').glob('*.py'))
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        for path in [root / 'extension.yaml', root / 'LICENSE-UPSTREAM.txt', *backend]:
            archive.write(path, path.relative_to(root).as_posix())
        archive.write(page, 'frontend/extension.mjs')
    return target


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--page', type=Path)
    args = parser.parse_args()
    print(build(args.destination, args.page))
