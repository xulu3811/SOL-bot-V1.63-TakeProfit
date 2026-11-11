set -eu
/opt/sol-bot/venv/bin/python - <<'PY'
from pathlib import Path
import hashlib,json
root=Path('/opt/sol-bot/releases/p3-20261111T042851Z')
result={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['backend','tests','deploy'] for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
print(json.dumps(result,sort_keys=True))
PY
systemctl show sol-shadow sui-strategy solbot-market solbot-sui-market solbot-micro-sol solbot-micro-sui -p Id -p ExecStart -p WorkingDirectory -p MainPID -p ActiveState
readlink -f /opt/sol-bot/p3
