set -eu
cd /opt/sol-bot/p2
sudo -u solbot env PYTHONPATH=/opt/sol-bot/p2/backend/src PYTHONDONTWRITEBYTECODE=1 nice -n 15 /opt/sol-bot/venv/bin/python tests/integration/p2_live_replay.py
