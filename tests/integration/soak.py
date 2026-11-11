"""Bounded loopback monitoring; emits evidence every minute for a ten-minute run."""
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

samples=[]
start=time.monotonic()
for index in range(11):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5) as response:
            health=json.load(response)
    except urllib.error.HTTPError as error:
        health=json.load(error)
    raw=subprocess.check_output(['systemctl','show','solbot-market.service','-p','CPUUsageNSec','-p','MemoryCurrent','-p','MemoryPeak','-p','NRestarts','-p','ActiveState'],text=True)
    metrics=dict(line.split('=',1) for line in raw.splitlines())
    paths=[Path('/var/lib/sol-bot/database/market.sqlite'+suffix) for suffix in ('','-wal')]
    health.get('collector',{}).pop('symbol_info',None)
    sample={'elapsed_s':round(time.monotonic()-start,1),'ready':health['ready'],'reasons':health.get('reasons'),
            'service':metrics,'file_bytes':{p.name:p.stat().st_size if p.exists() else 0 for p in paths},
            'collector':health.get('collector',{})}
    samples.append(sample)
    print(json.dumps(sample),flush=True)
    if index<10: time.sleep(60)
