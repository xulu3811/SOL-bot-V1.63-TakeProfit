set -eu
systemctl show sol-bot-backup.service -p ExecStart -p User -p Group -p Result
journalctl -u sol-bot-backup.service -n 25 --no-pager
cat /opt/sol-bot/scripts/backup-sqlite.sh /opt/sol-bot/scripts/sqlite_backup.py
stat -c '%U:%G %a %n' /var/lib/sol-bot/database/*.sqlite /var/lib/sol-bot/backups
systemctl show solbot-market.service solbot-sui-market.service solbot-micro-sol.service solbot-micro-sui.service sol-shadow.service sui-strategy.service -p Id -p ActiveState -p MainPID -p MemoryCurrent -p CPUUsageNSec -p NRestarts
free -m
df -h /
