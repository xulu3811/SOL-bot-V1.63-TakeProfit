set -eu
stat -c '%A %U %G %n' /var/lib/sol-bot/database /var/lib/sol-bot/database/sol-shadow.sqlite*
getfacl -p /var/lib/sol-bot/database/sol-shadow.sqlite /var/lib/sol-bot/database/sol-shadow.sqlite-shm 2>/dev/null || true
journalctl -u sol-shadow --since '2026-11-13 00:00:00' --no-pager -n 50
