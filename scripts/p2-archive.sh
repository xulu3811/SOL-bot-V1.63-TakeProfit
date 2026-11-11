set -eu
umask 027
target=/var/lib/sol-bot/audits/p2-20261111
if test -e "$target"; then echo 'Archive target already exists; refusing overwrite'; exit 1; fi
install -d -o root -g solbot -m750 "$target"
tar -xzf /tmp/solbot-p1-package.tar.gz -C "$target" --no-same-owner
chown -R root:solbot "$target"
find "$target" -type d -exec chmod 750 {} +
find "$target" -type f -exec chmod 640 {} +
/opt/sol-bot/venv/bin/python - <<'PY'
from pathlib import Path
import hashlib,json
root=Path('/var/lib/sol-bot/audits/p2-20261111')
manifest=json.loads((root/'data/p2-evidence-manifest.json').read_text())
for name,digest in manifest.items():
 path=(root/name).resolve()
 assert path.is_relative_to(root)
 assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
print(json.dumps({'evidence_archive':str(root),'verified_files':len(manifest),'result':'PASS'}))
PY
