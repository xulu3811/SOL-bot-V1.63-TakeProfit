set -eu
umask 027
stamp=$(date -u +%Y%m%dT%H%M%SZ)
release=/opt/sol-bot/releases/p2-$stamp
install -d -o root -g solbot -m750 "$release"
tar -xzf /tmp/solbot-p1-package.tar.gz -C "$release" --no-same-owner
chown -R root:solbot "$release"
find "$release" -type d -exec chmod 750 {} +
find "$release" -type f -exec chmod 640 {} +
cd "$release"
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/unit -v
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/integration -v
if test -e /opt/sol-bot/p2 && ! test -L /opt/sol-bot/p2; then echo 'Unmanaged P2 directory'; exit 1; fi
if test -L /opt/sol-bot/p2; then readlink /opt/sol-bot/p2 > "$release/previous-p2.txt"; fi
ln -sfn "$release" /opt/sol-bot/p2
systemd-analyze verify deploy/solbot-sui-market.service
install -m644 deploy/solbot-sui-market.service /etc/systemd/system/solbot-sui-market.service
systemctl daemon-reload
systemctl enable --now solbot-sui-market.service
systemctl is-active solbot-market.service solbot-sui-market.service sol-bot-backup.timer sol-bot-health.timer
