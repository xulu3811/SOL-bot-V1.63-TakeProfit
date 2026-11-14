set -eu
sudo -u solbot-app env PYTHONPATH=/opt/sol-bot/p5/backend/src /opt/sol-bot/venv/bin/python - <<'PY'
from solbot.app_api.data import DataReader
r=DataReader().shadow_status()
print({k:r.get(k) for k in ['available','synced','engine_status','reason','heartbeat_at','last_market_at','equity','total_trades','last_error']})
assert r['available'] and r['synced'], r
PY
ls -l /var/lib/sol-bot/database/sol-shadow*.sqlite*
