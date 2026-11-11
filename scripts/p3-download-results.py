"""Fetch only the authorized public replay artifact, never credential files."""
from pathlib import Path
import json
from remote_admin import connect

target=Path(__file__).resolve().parents[1]/'data/p3-real-replay-result.json'
with connect() as transport:
    channel=transport.open_session(timeout=20)
    channel.settimeout(60)
    channel.exec_command('sudo -n cat /var/lib/sol-bot/research/p3-real-replay-result.json')
    with target.open('wb') as output:
        while True:
            chunk=channel.recv(65536)
            if not chunk:break
            output.write(chunk)
    if channel.recv_exit_status()!=0:raise RuntimeError('public artifact download failed')
value=json.loads(target.read_text())
print(json.dumps({'status':value['status'],'bytes':target.stat().st_size,'archive':value['archive_dir']}))
