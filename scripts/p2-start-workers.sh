set -eu
cd /opt/sol-bot/p2
PYTHONPATH=/opt/sol-bot/p2/backend/src /opt/sol-bot/venv/bin/python -m solbot.execution.live --database /var/lib/sol-bot/database/sui-live-control.sqlite status
PYTHONPATH=/opt/sol-bot/p2/backend/src /opt/sol-bot/venv/bin/python -m solbot.execution.live --database /var/lib/sol-bot/database/sui-live-control.sqlite on
chown root:solbot /var/lib/sol-bot/database/sui-live-control.sqlite*
chmod 640 /var/lib/sol-bot/database/sui-live-control.sqlite*
for name in sol-shadow sui-strategy; do
 systemd-analyze verify "deploy/$name.service"
 if test -f "/etc/systemd/system/$name.service"; then cp -p "/etc/systemd/system/$name.service" "deploy/$name.previous.unit"; fi
 install -m644 "deploy/$name.service" "/etc/systemd/system/$name.service"
done
systemctl daemon-reload
systemctl restart solbot-sui-market.service
systemctl enable --now sol-shadow.service sui-strategy.service
systemctl is-active sol-shadow.service sui-strategy.service solbot-market.service solbot-sui-market.service
