set -eu
python3 - <<'PY'
import sqlite3,os
for name in ['market','sol-shadow-strategy','sol-shadow','sol-math-audit']:
 src=sqlite3.connect('file:/var/lib/sol-bot/database/'+name+'.sqlite?mode=ro',uri=True)
 dst=sqlite3.connect('/tmp/rebound-'+name+'.sqlite')
 src.backup(dst);dst.close();src.close();os.chmod('/tmp/rebound-'+name+'.sqlite',0o644)
PY
tar -czf /tmp/rebound-source.tar.gz -C /opt/sol-bot/p3 backend/src/solbot
chmod 644 /tmp/rebound-source.tar.gz
cat /var/lib/sol-bot/sol-shadow-status.json
