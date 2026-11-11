set -eu
cd /opt/sol-bot/p3
env PYTHONPATH=/opt/sol-bot/p3/backend/src PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python scripts/p3-postcheck.py
systemctl start sol-bot-backup.service sol-bot-health.service
systemctl show solbot-market.service solbot-sui-market.service solbot-micro-sol.service solbot-micro-sui.service sol-shadow.service sui-strategy.service -p Id -p ActiveState -p MainPID -p MemoryCurrent -p CPUUsageNSec -p NRestarts
systemctl show sol-bot-backup.service sol-bot-health.service -p Id -p Result -p ExecMainStatus
free -m
df -h /
du -h /var/lib/sol-bot/database/*math-audit* /var/lib/sol-bot/database/sol-shadow.sqlite*
