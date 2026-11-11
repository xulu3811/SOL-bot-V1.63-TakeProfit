set -eu
mkdir -p /opt/sol-bot/p1-staging
tar -xzf /tmp/solbot-p1-package.tar.gz -C /opt/sol-bot/p1-staging --no-same-owner
chown -R root:solbot /opt/sol-bot/p1-staging
find /opt/sol-bot/p1-staging -type d -exec chmod 750 {} +
find /opt/sol-bot/p1-staging -type f -exec chmod 640 {} +
cd /opt/sol-bot/p1-staging
sudo -u solbot env PYTHONPATH=/opt/sol-bot/p1-staging/backend/src PYTHONDONTWRITEBYTECODE=1 timeout 90 /opt/sol-bot/venv/bin/python -m unittest discover -s tests/integration -v
