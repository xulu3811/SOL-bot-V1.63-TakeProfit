set -eu
systemctl restart sol-r2-shadow.service
sleep 3
cat /var/lib/sol-bot/r2/status.json
journalctl -u sol-r2-shadow.service --no-pager -n 8
