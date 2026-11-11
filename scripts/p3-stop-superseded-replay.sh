set -eu
/opt/sol-bot/venv/bin/python - <<'PY'
from pathlib import Path
import os,signal
for entry in Path('/proc').iterdir():
 if not entry.name.isdigit():continue
 try:args=(entry/'cmdline').read_bytes().split(b'\0')
 except (FileNotFoundError,ProcessLookupError,PermissionError):continue
 if len(args)>2 and args[0]==b'/opt/sol-bot/venv/bin/python' and args[1]==b'scripts/p3-real-replay.py' and b'/var/lib/sol-bot/research/p3-real-replay-result.json' in args:
  os.kill(int(entry.name),signal.SIGTERM);print('STOPPED_SUPERSEDED_REPLAY',entry.name)
PY
