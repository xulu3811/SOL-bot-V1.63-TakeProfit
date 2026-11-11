set -eu
umask 027
stamp=$(date -u +%Y%m%dT%H%M%SZ)
release=/opt/sol-bot/releases/p1-$stamp
install -d -o root -g solbot -m 750 /opt/sol-bot/releases
mkdir -p "$release"
tar -xzf /tmp/solbot-p1-package.tar.gz -C "$release" --no-same-owner
chown -R root:solbot "$release"
find "$release" -type d -exec chmod 750 {} +
find "$release" -type f -exec chmod 640 {} +
cd "$release"
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/unit -v
sudo -u solbot env PYTHONPATH="$release/backend/src" PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/integration -v
systemd-analyze verify deploy/solbot-market.service
# Preserve an existing deployment before switching only this task's source links.
if test -e /opt/sol-bot/backend/pyproject.toml && ! test -L /opt/sol-bot/backend; then
  echo 'Unmanaged backend exists; refusing overwrite'; exit 1
fi
for dir in backend tests deploy docs; do
  if test -L /opt/sol-bot/$dir; then
    readlink /opt/sol-bot/$dir > "$release/previous-$dir.txt"
  elif test -d /opt/sol-bot/$dir; then
    rmdir /opt/sol-bot/$dir
  fi
  ln -sfn "$release/$dir" /opt/sol-bot/$dir
done
cd /opt/sol-bot
if test -f /etc/systemd/system/solbot-market.service; then cp -p /etc/systemd/system/solbot-market.service "$release/previous-service.unit"; fi
install -m 644 deploy/solbot-market.service /etc/systemd/system/solbot-market.service
systemctl daemon-reload
systemctl enable solbot-market.service
systemctl restart solbot-market.service
systemctl is-active solbot-market.service ssh sol-bot-backup.timer sol-bot-health.timer
