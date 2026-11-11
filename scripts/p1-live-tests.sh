set -eu
cd /opt/sol-bot
sudo -u solbot /opt/sol-bot/venv/bin/python - <<'PY'
import time,urllib.request,urllib.error
for attempt in range(36):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5) as r:
   if r.status==200: break
 except (urllib.error.URLError,TimeoutError): pass
 time.sleep(5)
else: raise SystemExit('Readiness did not converge within 180s')
PY
sudo -u solbot env PYTHONPATH=/opt/sol-bot/backend/src PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python tests/integration/live_evidence.py
sudo -u solbot env PYTHONPATH=/opt/sol-bot/backend/src PYTHONDONTWRITEBYTECODE=1 nice -n 10 /opt/sol-bot/venv/bin/python tests/integration/resource_probe.py
