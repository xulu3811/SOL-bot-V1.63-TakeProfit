set -eu
python3 - <<'PY'
import json
from pathlib import Path
from urllib.parse import urlparse
items=json.load(open('/tmp/solbot-p1-deps.json'))['install']
lines=['# CPython 3.14 Linux x86_64 wheels, resolved from official PyPI.']
for item in items:
 if urlparse(item['download_info']['url']).hostname != 'files.pythonhosted.org':
  raise SystemExit('Unexpected package source')
 lines.append(item['metadata']['name']+'=='+item['metadata']['version']+' --hash=sha256:'+item['download_info']['archive_info']['hashes']['sha256'])
Path('/tmp/solbot-p1-requirements.lock').write_text('\n'.join(lines)+'\n')
PY
/opt/sol-bot/venv/bin/python -m pip --isolated install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r /tmp/solbot-p1-requirements.lock --disable-pip-version-check
/opt/sol-bot/venv/bin/python -m pip check
cat /opt/sol-bot/scripts/backup-sqlite.sh /opt/sol-bot/scripts/sqlite_backup.py
