set -eu
chgrp solbot /var/lib/sol-bot/backups/p3-migration-20261111T045417Z /var/lib/sol-bot/backups/p3-acceptance-20261111T045453Z
chmod 750 /var/lib/sol-bot/backups/p3-migration-20261111T045417Z /var/lib/sol-bot/backups/p3-acceptance-20261111T045453Z
systemctl start sol-bot-backup.service sol-bot-health.service
systemctl show sol-bot-backup.service sol-bot-health.service -p Id -p Result -p ExecMainStatus
journalctl -u sol-shadow.service -u sui-strategy.service --since '2026-11-11 12:54:00' --no-pager -n 75
