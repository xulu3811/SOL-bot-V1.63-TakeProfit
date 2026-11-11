set -eu
/opt/sol-bot/venv/bin/python - <<'PY'
from pathlib import Path
import sqlite3,json
from contextlib import closing
base=Path('/var/lib/sol-bot/research')
archive=sorted(base.glob('p3-real-replay-*'))[-1]
print('ARCHIVE',archive)
for path in archive.glob('replay-*/*/*.sqlite'):
 with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
  tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if 'decisions' in tables:print(str(path.relative_to(archive)),db.execute('SELECT count(*) FROM decisions').fetchone()[0])
for path in archive.glob('replay-*/*/*.jsonl'):print(path.name,path.stat().st_size)
PY
ps -eo pid,etime,pcpu,rss,args | grep '[p]ython scripts/p3-real-replay.py'
