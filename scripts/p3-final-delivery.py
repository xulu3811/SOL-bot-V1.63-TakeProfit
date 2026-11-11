"""Archive frozen P3 source, reports and selected non-secret evidence."""
from pathlib import Path
import hashlib,json,tarfile
root=Path(__file__).resolve().parents[1]
paths=[]
for folder in ('backend','tests','deploy'):
    paths.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.sqlite','.db') and not p.name.startswith('.env'))
paths.extend((root/'docs').glob('p3*'))
paths.extend((root/'scripts').glob('p3-*'))
paths.append(root/'scripts/remote_admin.py')
evidence=('p3-baseline-diff.json','p3-micro-observation.txt','p3-comparison-synthetic.json','p3-independent-local-tests.txt',
    'p3-replay-independent-rerun.txt','p3-final-tests-v2.txt','p3-final-local-unit.txt','p3-final-local-integration.txt',
    'p3-migration-evidence.txt','p3-restart-restore-final.txt','p3-backup-fix.txt','p3-final-observation.txt',
    'p3-final-source-manifest.json','p3-real-replay-result.json','p3-real-replay-summary.json')
paths.extend(root/'data'/name for name in evidence)
manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths)) if p.is_file()}
manifest_path=root/'data/p3-delivery-manifest.json'
manifest_path.write_text(json.dumps({'runtime_release':'p3-20261111T045723Z','files':manifest},indent=2),encoding='utf-8')
output=root/'data/p3-final-delivery-20261111.tar.gz'
with tarfile.open(output,'w:gz') as archive:
    for name in manifest:archive.add(root/name,arcname=name)
    archive.add(manifest_path,arcname='data/p3-delivery-manifest.json')
value={'file':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(manifest)}
(root/'data/p3-delivery-sha256.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
print(json.dumps(value))
