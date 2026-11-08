"""Package only the extension source and self-contained page build."""
from pathlib import Path
import zipfile


def build():
    root = Path(__file__).resolve().parent
    target = root/'dist'/'lan-kline-1.1.2.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(root/'extension.yaml', 'extension.yaml')
        for path in sorted((root/'backend').glob('*.py')):
            archive.write(path, 'backend/'+path.name)
        archive.write(root/'dist'/'extension.js', 'frontend/extension.mjs')
    return target


if __name__ == '__main__':
    print(build())
