from pathlib import Path
import tarfile
import hashlib
import json
root=Path(__file__).resolve().parents[1]
paths=[]
for folder in ('backend','tests','deploy','docs'):
    paths.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.sqlite','.db'))
paths.extend((root/'scripts').glob('p4-*'))
paths.extend((root/'scripts').glob('p3-*.py'))
manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
(root/'data/p4-source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
with tarfile.open(root/'data/p4-package.tar.gz','w:gz') as archive:
    for path in paths: archive.add(path,arcname=path.relative_to(root).as_posix())
    archive.add(root/'data/p4-source-manifest.json',arcname='data/p4-source-manifest.json')
print(root/'data/p4-package.tar.gz')

