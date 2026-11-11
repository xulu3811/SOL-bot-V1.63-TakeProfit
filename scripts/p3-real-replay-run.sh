set -eu
cd /opt/sol-bot/p3
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive=/var/lib/sol-bot/research/p3-real-replay-$stamp
install -d -o solbot -g solbot -m750 /var/lib/sol-bot/research
sudo -u solbot env PYTHONPATH=/opt/sol-bot/p3/backend/src PYTHONDONTWRITEBYTECODE=1 nice -n 15 /opt/sol-bot/venv/bin/python scripts/p3-real-replay.py --data-dir /var/lib/sol-bot/database --output /var/lib/sol-bot/research/p3-real-replay-result.json --archive-dir "$archive"
cat /var/lib/sol-bot/research/p3-real-replay-result.json
