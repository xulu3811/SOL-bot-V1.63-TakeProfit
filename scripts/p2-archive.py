"""Allowlisted P2 evidence bundle for the user's existing server."""
from pathlib import Path
import hashlib,json,tarfile
root=Path(__file__).resolve().parents[1]
paths=[root/'docs/P2-验收报告.md',root/'docs/p2-independent-qa.md',root/'docs/p2-operations.md',root/'docs/p2-contract.md']
paths += sorted((root/'data').glob('p2-*.txt'))
paths += [root/'data/p2-source-manifest.json',root/'data/p2-synthetic-examples.json']
manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
manifest_path=root/'data/p2-evidence-manifest.json'
manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
with tarfile.open(root/'data/p2-acceptance.tar.gz','w:gz') as archive:
    for p in paths+[manifest_path]:archive.add(p,arcname=p.relative_to(root).as_posix())
print('Allowlisted evidence files:',len(paths)+1)
