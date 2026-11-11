set -eu
umask 027
stamp=$(date -u +%Y%m%dT%H%M%SZ)
release=/opt/sol-bot/releases/p3-$stamp
install -d -o root -g solbot -m750 "$release"
tar -xzf /tmp/solbot-p1-package.tar.gz -C "$release" --no-same-owner
chown -R root:solbot "$release"
find "$release" -type d -exec chmod 750 {} +
find "$release" -type f -exec chmod 640 {} +
cd "$release"
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/unit -v
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/integration -v
if test -e /opt/sol-bot/p3 && ! test -L /opt/sol-bot/p3; then echo 'Unmanaged P3 directory'; exit 1; fi
if test -L /opt/sol-bot/p3; then readlink /opt/sol-bot/p3 > "$release/previous-p3.txt"; fi
ln -sfn "$release" /opt/sol-bot/p3
for name in solbot-micro-sol solbot-micro-sui; do
 systemd-analyze verify "deploy/$name.service"
 install -m644 "deploy/$name.service" "/etc/systemd/system/$name.service"
done
systemctl daemon-reload
systemctl enable --now solbot-micro-sol.service solbot-micro-sui.service
systemctl is-active solbot-market.service solbot-sui-market.service solbot-micro-sol.service solbot-micro-sui.service sol-bot-backup.timer sol-bot-health.timer
printf '%s\n' "$release"
