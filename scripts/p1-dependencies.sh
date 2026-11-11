set -eu
find /opt/sol-bot/backend /opt/sol-bot/scripts -maxdepth 2 -type f -printf '%p\n'
systemctl cat sol-bot-backup.service sol-bot-health.service sol-bot-backup.timer sol-bot-health.timer
find /etc/apt/sources.list.d -maxdepth 1 -type f -printf '%f\n'
/opt/sol-bot/venv/bin/python -m pip --version
/opt/sol-bot/venv/bin/python -m pip --isolated install --dry-run --ignore-installed --only-binary=:all: --index-url https://pypi.org/simple --report /tmp/solbot-p1-deps.json fastapi httpx websockets uvicorn
python3 - <<'PY'
import json
report=json.load(open('/tmp/solbot-p1-deps.json'))
for x in report['install']:
 print('LOCK',x['metadata']['name']+'=='+x['metadata']['version'],x['metadata'].get('requires_python'),x['download_info']['archive_info']['hashes']['sha256'])
PY
