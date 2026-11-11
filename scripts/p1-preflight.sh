set -eu
date -u +%FT%TZ
free -m
df -h /
swapon --show
sysctl vm.swappiness
python3 --version
python3 -m pip --version || true
for path in /opt/sol-bot /opt/sol-bot/venv /var/lib/sol-bot /var/log/sol-bot /etc/sol-bot; do
  if test -e "$path"; then stat -c '%a %U:%G %n' "$path"; find "$path" -maxdepth 2 -type d; else printf 'MISSING %s\n' "$path"; fi
done
getent passwd solbot | cut -d: -f1,3,4,6,7 || true
systemctl list-unit-files 'solbot*' 'sol-bot*' --no-pager
systemctl is-active ssh cron chrony
dpkg-query -W python3-venv python3.14-venv 2>/dev/null || true
ss -lntH | awk '{print $4}' | sed -E 's/([0-9]+\.){3}[0-9]+/[ipv4]/g'
