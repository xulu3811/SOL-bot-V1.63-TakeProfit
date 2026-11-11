set -eu
umask 027
test "$(readlink -f /opt/sol-bot/p4)" = /opt/sol-bot/releases/p4-20261111T054542Z
stamp=$(date -u +%Y%m%dT%H%M%SZ)
release=/opt/sol-bot/releases/p5-$stamp
install -d -o root -g solbot -m750 "$release"
tar -xzf /tmp/solbot-p5-package.tar.gz -C "$release" --no-same-owner
chown -R root:solbot "$release"
find "$release" -type d -exec chmod 750 {} +
find "$release" -type f -exec chmod 640 {} +
cd "$release"
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/unit -v > data/p5-unit.txt 2>&1 || { tail -80 data/p5-unit.txt; exit 1; }
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/integration -v > data/p5-integration.txt 2>&1 || { cat data/p5-integration.txt; exit 1; }
tail -4 data/p5-unit.txt
tail -4 data/p5-integration.txt
PYTHONPATH="$release/backend/src" /opt/sol-bot/venv/bin/python scripts/p5-test-evidence.py > data/p5-evidence-tests.txt 2>&1
backup=/var/lib/sol-bot/backups/p5-before-$stamp
install -d -o root -g solbot -m750 "$backup"
PYTHONPATH="$release/backend/src" /opt/sol-bot/venv/bin/python - "$backup" <<'PY' > data/p5-backup.json
from pathlib import Path
from contextlib import closing
import sqlite3,json,sys,hashlib
from solbot.accounts.allocation_ledger import AllocationLedger
base=Path('/var/lib/sol-bot/database');out=Path(sys.argv[1]);result=[]
for name in ('sui-guardian-p4.sqlite','sui-live-control.sqlite','sol-shadow.sqlite'):
 with closing(sqlite3.connect((base/name).as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(out/name)) as dst: src.backup(dst)
 with closing(sqlite3.connect(out/name)) as src,closing(sqlite3.connect(out/(name+'.restored'))) as dst:
  src.backup(dst)
  hashes=[hashlib.sha256('\n'.join(d.iterdump()).encode()).hexdigest() for d in (src,dst)]
  assert hashes[0]==hashes[1] and dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  result.append({'file':name,'sha256':hashes[0],'restored':True})
print(json.dumps({'backup':str(out),'databases':result,'guardian':AllocationLedger(base/'sui-guardian-p4.sqlite').snapshot()}))
orders=Path('/var/lib/sol-bot-execution/orders.sqlite')
if orders.exists():
 from solbot.execution.p5_store import OrderStore
 print(json.dumps(OrderStore(orders).backup(out/'orders.sqlite')))
PY
if ! id solbot-exec >/dev/null 2>&1; then useradd --system --no-create-home --shell /usr/sbin/nologin solbot-exec; fi
usermod -a -G solbot solbot-exec
ln -sfn "$release" /opt/sol-bot/p5
for name in solbot-guardian-ipc solbot-execution-p5; do
 systemd-analyze verify "deploy/$name.service"
 install -m644 "deploy/$name.service" "/etc/systemd/system/$name.service"
done
systemctl daemon-reload
systemctl enable --now solbot-guardian-ipc.service solbot-execution-p5.service
systemctl restart solbot-guardian-ipc.service solbot-execution-p5.service
sleep 7
systemctl is-active solbot-guardian-ipc.service solbot-execution-p5.service
cat /var/lib/sol-bot-execution/status.json
printf '\nRELEASE=%s\n' "$release"
