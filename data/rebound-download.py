import sys
sys.path.insert(0,'scripts')
from remote_admin import connect
from pathlib import Path
import paramiko,tarfile
out=Path('data/rebound-20261113');out.mkdir(exist_ok=True)
with connect() as t:
 with paramiko.SFTPClient.from_transport(t) as s:
  for name in ['market.sqlite','sol-shadow-strategy.sqlite','sol-shadow.sqlite','sol-math-audit.sqlite','source.tar.gz']:
   s.get('/tmp/rebound-'+name,str(out/name))
with tarfile.open(out/'source.tar.gz') as t:t.extractall(out/'deployed',filter='data')
print('snapshots downloaded')
