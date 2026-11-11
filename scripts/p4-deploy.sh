set -eu
umask 027
test "$(readlink -f /opt/sol-bot/p3)" = /opt/sol-bot/releases/p3-20261111T045723Z
stamp=$(date -u +%Y%m%dT%H%M%SZ)
release=/opt/sol-bot/releases/p4-$stamp
install -d -o root -g solbot -m750 "$release"
tar -xzf /tmp/solbot-p4-package.tar.gz -C "$release" --no-same-owner
chown -R root:solbot "$release"
find "$release" -type d -exec chmod 750 {} +
find "$release" -type f -exec chmod 640 {} +
cd "$release"
mkdir -p data
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/unit -v > data/p4-unit.txt 2>&1 || { cat data/p4-unit.txt; exit 1; }
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/integration -v > data/p4-integration.txt 2>&1 || { cat data/p4-integration.txt; exit 1; }
tail -4 data/p4-unit.txt
tail -4 data/p4-integration.txt
PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python scripts/p4-test-evidence.py > data/p4-evidence-tests.txt 2>&1
backup=/var/lib/sol-bot/backups/p4-migration-$stamp
if test -f /var/lib/sol-bot/database/sui-guardian-p4.sqlite; then
 systemctl stop solbot-guardian.service
 install -d -o root -g solbot -m750 "$backup"
 PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python - "$backup" <<'PY' > data/p4-migration.json
import json,sys
from pathlib import Path
from solbot.accounts.allocation_ledger import AllocationLedger
l=AllocationLedger('/var/lib/sol-bot/database/sui-guardian-p4.sqlite')
print(json.dumps({'backup':l.backup_restore(Path(sys.argv[1])/'p4-before-upgrade.sqlite'),'state':l.snapshot()}))
PY
else
 PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python scripts/p4-migrate.py "$backup" > data/p4-migration.json
fi
chown -R root:solbot "$backup"
chmod 750 "$backup"
chown solbot:solbot /var/lib/sol-bot/database/sui-guardian-p4.sqlite*
chmod 640 /var/lib/sol-bot/database/sui-guardian-p4.sqlite*
systemd-analyze verify deploy/solbot-guardian.service
ln -sfn "$release" /opt/sol-bot/p4
install -m644 deploy/solbot-guardian.service /etc/systemd/system/solbot-guardian.service
systemctl daemon-reload
systemctl enable --now solbot-guardian.service
sleep 6
systemctl is-active solbot-guardian.service
cat /var/lib/sol-bot/guardian-status.json
printf '\nRELEASE=%s\nBACKUP=%s\n' "$release" "$backup"
