set -eu
cd /opt/sol-bot
sudo -u solbot /opt/sol-bot/venv/bin/python tests/integration/soak.py
