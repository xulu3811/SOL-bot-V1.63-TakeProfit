set -eu
python3 - <<'PY'
from pathlib import Path
import shutil,os
api=Path('/opt/sol-bot/p5/backend/src/solbot/app_api/data.py')
src=Path('/opt/sol-bot/r2-v2-100/backend/src/solbot/app_api/data.py').read_text()
old=api.read_text()
if "'r2_status':" not in old:
    anchor='DEFAULTS={'
    old=old.replace(anchor,anchor+"'r2_status':Path('/var/lib/sol-bot/r2/status.json'),",1)
    start=src.index('        result = dict(result, **health)')
    end=src.index('    def shadow_orders',start)
    assert old.count('        return dict(result, **health)')==1
    old=old.replace('        return dict(result, **health)\n',src[start:end])
    compile(old,str(api),'exec')
    shutil.copy2(api,api.with_suffix('.before-r2-panel'))
    tmp=api.with_suffix('.staged')
    tmp.write_text(old)
    tmp.chmod(0o644)
    os.replace(tmp,api)
PY
systemctl restart solbot-app.service
sudo -u solbot-app env PYTHONPATH=/opt/sol-bot/p5/backend/src /opt/sol-bot/venv/bin/python - <<'PY'
from solbot.app_api.data import DataReader
r=DataReader().shadow_status()
print('primary',r['synced'],'r2',r['r2']['synced'])
assert r['synced'] and r['r2']['synced']
PY
