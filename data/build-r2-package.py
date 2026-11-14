from pathlib import Path
import tarfile,json,hashlib
root=Path('backend/src/solbot')
with tarfile.open('data/r2-package.tar.gz','w:gz') as archive:
 for path in root.rglob('*.py'):
  archive.add(path,arcname=str(path).replace('\\','/'))
 archive.add('deploy/sol-r2-shadow.service',arcname='sol-r2-shadow.service')
manifest={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.py')}
Path('data/rebound-20261113/r2-build-manifest.json').write_text(json.dumps(manifest,indent=2))
