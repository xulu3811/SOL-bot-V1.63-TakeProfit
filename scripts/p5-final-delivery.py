from pathlib import Path
import hashlib
import json
import tarfile
root=Path(__file__).resolve().parents[1]
paths=[]
for folder in ('backend','tests','deploy'):
    paths.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.sqlite','.db'))
paths.extend((root/'docs').glob('p5-*.md'))
paths.extend((root/'scripts').glob('p5-*'))
paths.extend((root/'scripts').glob('p3-*.py'))
paths.extend(p for p in (root/'data/p5-server').rglob('*') if p.is_file())
paths.extend((root/'data').glob('p5-*.txt'))
paths=sorted(set(paths))
manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
mp=root/'data/p5-delivery-manifest.json'; mp.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
output=root/'data/p5-final-delivery.tar.gz'
with tarfile.open(output,'w:gz') as archive:
    for p in paths+[mp]: archive.add(p,arcname=p.relative_to(root).as_posix())
record={'file':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(paths)+1,'runtime':'p5-20261111T065321Z'}
(root/'data/p5-delivery-sha256.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))

