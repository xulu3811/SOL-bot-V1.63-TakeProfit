set -eu
cat /var/lib/sol-bot/sol-shadow-status.json
ls -l /var/lib/sol-bot/database/sol-shadow.sqlite*
systemctl show sol-shadow -p User -p Group -p SupplementaryGroups -p ReadWritePaths
