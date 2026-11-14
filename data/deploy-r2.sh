set -eu
test ! -e /opt/sol-bot/r2-v1
mkdir /opt/sol-bot/r2-v1
tar -xzf /tmp/solbot-p1-package.tar.gz -C /opt/sol-bot/r2-v1
install -d -o solbot -g solbot -m 750 /var/lib/sol-bot/r2
install -m 644 /opt/sol-bot/r2-v1/sol-r2-shadow.service /etc/systemd/system/sol-r2-shadow.service
PYTHONPATH=/opt/sol-bot/r2-v1/backend/src /opt/sol-bot/venv/bin/python -m solbot.r2_runtime --help >/dev/null
systemctl daemon-reload
systemctl enable --now sol-r2-shadow.service
systemctl is-active sol-r2-shadow.service
