set -eu
cd /opt/sol-bot/p3
env PYTHONPATH=/opt/sol-bot/p3/backend/src PYTHONDONTWRITEBYTECODE=1 /opt/sol-bot/venv/bin/python scripts/p3-migrate.py
systemctl show sol-shadow.service sui-strategy.service -p Id -p MainPID -p ExecStart -p WorkingDirectory -p ActiveState
