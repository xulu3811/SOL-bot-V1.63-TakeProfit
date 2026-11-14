set -eu
release=/opt/sol-bot/r2-v2-100
test ! -e "$release"
mkdir "$release"
tar -xzf /tmp/solbot-p1-package.tar.gz -C "$release"
PYTHONPATH="$release/backend/src" /opt/sol-bot/venv/bin/python -c 'from solbot.execution.r2_shadow import R2ShadowExecutor as R; assert str(R.minimum_notional)=="100" and str(R.risk_budget)=="0.002"'
cp /etc/systemd/system/sol-r2-shadow.service "$release/previous.service"
install -m 644 "$release/sol-r2-shadow.service" /etc/systemd/system/sol-r2-shadow.service
systemctl daemon-reload
systemctl restart sol-r2-shadow.service
sleep 4
systemctl is-active sol-r2-shadow.service
python3 - <<'PY'
import json,time
from pathlib import Path
status=json.loads(Path('/var/lib/sol-bot/r2/status.json').read_text())
print({k:status.get(k) for k in ('status','updated_at','live_authorized','error')})
assert status['status']=='ready' and 0<=time.time()*1000-status['updated_at']<10000 and not status['live_authorized']
PY
