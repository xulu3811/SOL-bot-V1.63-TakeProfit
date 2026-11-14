set -eu
python3 - <<'PY'
from pathlib import Path
p=Path('/opt/sol-bot/p5/backend/src/solbot/app_api/data.py')
s=p.read_text()
for a,b in [('    def read(', '    def _owner('),('    def shadow_status(', '    def shadow_orders(')]:
 start=s.index(a);end=s.index(b,start);print(s[start:end])
PY
