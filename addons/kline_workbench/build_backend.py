"""Package only the extension source and self-contained page build."""
from pathlib import Path
import zipfile


def build():
    root = Path(__file__).resolve().parent
    target = root/'dist'/'lan-kline-2.0.0.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(root/'extension.yaml', 'extension.yaml')
        for path in [root/'backend'/name for name in ('__init__.py','extension.py','service.py','publisher.py','market_client.py','bridge_lease.py')]:
            archive.write(path, 'backend/'+path.name)
        archive.write(root/'dist'/'extension.js', 'frontend/extension.mjs')
    return target


if __name__ == '__main__':
    print(build())
