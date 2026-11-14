set -eu
date -u
systemctl list-units --all --type=service --no-pager | grep -E 'sol|sui'
ls -lh /var/lib/sol-bot/database
for s in sol-shadow sol-shadow-p3 solbot-market solbot-app; do systemctl show "$s" -p ExecStart -p Environment -p ActiveEnterTimestamp --no-pager; done
