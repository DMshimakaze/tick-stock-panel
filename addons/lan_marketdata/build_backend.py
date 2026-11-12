"""Build the official backend-only extension ZIP."""
from pathlib import Path
import zipfile


def build():
    root=Path(__file__).resolve().parent
    target=root/'dist'/'lan-marketdata-1.0.3.zip'
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(root/'extension.yaml','extension.yaml')
        for path in sorted((root/'backend').glob('*.py')):
            archive.write(path,'backend/'+path.name)
    return target


if __name__=='__main__':
    print(build())
