set -eu
date -u +%FT%TZ
sudo -u solbot env PYTHONPATH=/opt/sol-bot/p3/backend/src /opt/sol-bot/venv/bin/python - <<'PY'
from solbot.storage.microstructure import MicrostructureStore
from pathlib import Path
import json,time
for symbol,name in [('SOLUSDT','sol'),('SUIUSDT','sui')]:
 store=MicrostructureStore('/var/lib/sol-bot/database/'+name+'-micro.sqlite',symbol,read_only=True)
 value=store.latest()
 print(json.dumps({'symbol':symbol,'snapshot':value,'local_now_ms':time.time_ns()//1000000,'retained_sample':len(store.history(limit=1000))}))
PY
systemctl show solbot-micro-sol.service solbot-micro-sui.service solbot-market.service solbot-sui-market.service -p Id -p MainPID -p NRestarts -p MemoryCurrent -p ActiveState
free -m
