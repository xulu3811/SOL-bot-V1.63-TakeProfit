from pathlib import Path
import base64,hashlib,json
root=Path('backend/src/solbot');baseline=Path('data/rebound-20261113/deployed/backend/src/solbot/p3_runtime.py')
data=(root/'app_api/data.py').read_text(encoding='utf8')
health=data[data.index('    def _shadow_health(self):'):data.index('    def shadow_status(self):')]
payload={'runtime':(root/'p3_runtime.py').read_text(encoding='utf8'),'snapshot':(root/'storage/read_snapshot.py').read_text(encoding='utf8'),'health':health,'runtime_base':hashlib.sha256(baseline.read_bytes()).hexdigest()}
encoded=base64.b64encode(json.dumps(payload).encode()).decode()
script="""set -eu
python3 - <<'PY'
import base64,json,hashlib,os,shutil,time,py_compile
from pathlib import Path
p=json.loads(base64.b64decode('PAYLOAD'))
runtime=Path('/opt/sol-bot/p3/backend/src/solbot/p3_runtime.py')
api=Path('/opt/sol-bot/p5/backend/src/solbot/app_api/data.py')
snapshot=runtime.parent/'storage/read_snapshot.py'
if hashlib.sha256(runtime.read_bytes()).hexdigest()!=p['runtime_base']:raise RuntimeError('Runtime changed since baseline; review required')
s=api.read_text()
if 'def _shadow_health' in s:raise RuntimeError('Health patch already present; review required')
s=s.replace("'shadow':ROOT/'sol-shadow.sqlite'", "'shadow':ROOT/'sol-shadow-read.sqlite'")
s=s.replace("DEFAULTS={'SOLUSDT_market'", "DEFAULTS={'shadow_status':Path('/var/lib/sol-bot/sol-shadow-status.json'),'SOLUSDT_market'")
s=s.replace('    def shadow_status(self):',p['health']+'    def shadow_status(self):')
old='        return self._safe(load)\\n    def shadow_orders(self,limit=50,offset=0):'
new='''        result = self._safe(load)
        health = self._shadow_health()
        if not result.get('available'):
            health.update(synced=False, reason='SOURCE_UNAVAILABLE')
        return dict(result, **health)
    def shadow_orders(self,limit=50,offset=0):'''
if s.count(old)!=1:raise RuntimeError('API patch anchor mismatch')
s=s.replace(old,new)
backup=Path('/opt/sol-bot/rollback-shadow-health-'+str(int(time.time())))
backup.mkdir()
for path,content in [(runtime,p['runtime']),(api,s),(snapshot,p['snapshot'])]:
 compile(content,str(path),'exec')
 if path.exists():shutil.copy2(path,backup/(path.parent.name+'-'+path.name))
 temp=path.with_suffix('.staged');temp.write_text(content);temp.chmod(0o644);os.replace(temp,path)
print('backup',backup)
PY
systemctl restart sol-shadow.service
for i in $(seq 1 20); do
 if test -f /var/lib/sol-bot/database/sol-shadow-read.sqlite; then break; fi
 sleep 1
done
test -f /var/lib/sol-bot/database/sol-shadow-read.sqlite
systemctl restart solbot-app.service
systemctl is-active sol-shadow.service solbot-app.service
""".replace('PAYLOAD',encoded)
Path('data/deploy-shadow-health.sh').write_text(script,encoding='utf8')
