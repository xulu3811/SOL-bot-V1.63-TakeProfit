"""Summarize saved results without changing samples or rerunning research."""
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime,timezone
import json,hashlib
root=Path(__file__).resolve().parents[1]
path=root/'data/p3-real-replay-result.json';r=json.loads(path.read_text())
out={'status':r['status'],'source_file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
     'manifest_sha256':r['manifest_sha256'],'archive_dir':r['archive_dir'],'symbols':{},'comparison':{}}
for symbol,v in r['symbols'].items():
    p=v['strategy_passes'][0];bounds=r['bounds'][symbol]
    out['symbols'][symbol]={'deterministic':v['deterministic'],'counts':p['report']['counts'],'reasons':p['report']['reasons'],
       'decisions':p['decisions'],'snapshots':p['snapshots'],'depth_coverage':p['depth_coverage'],'math':v['math_passes'][0],
       'utc_bounds':{k:datetime.fromtimestamp(t/1000,timezone.utc).isoformat() for k,t in bounds.items()}}
scenarios=r['real_close_path_comparison']['scenarios']
for scheme in ('FIXED_0_5','FIXED_1_0','ATR','APTS'):
    models=[s['comparison']['models'][scheme] for s in scenarios]
    out['comparison'][scheme]={'windows':len(models),'sum_hypothetical_net_pnl':str(sum((D(m['net_pnl']) for m in models),D(0))),
        'largest_window_drawdown':str(max(D(m['max_equity_drawdown']) for m in models)),
        'early_exits_ex_post':sum(m['early_exit_ex_post'] for m in models)}
out['apts_minus_fixed_1_0']=[{'entry_time_ms':s['entry_time_ms'],'difference':str(D(s['comparison']['models']['APTS']['net_pnl'])-D(s['comparison']['models']['FIXED_1_0']['net_pnl'])),
    'apts_reason':s['comparison']['models']['APTS']['reason'],'apts_net':s['comparison']['models']['APTS']['net_pnl'],'fixed_net':s['comparison']['models']['FIXED_1_0']['net_pnl']} for s in scenarios]
(root/'data/p3-real-replay-summary.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'comparison':out['comparison'],'apts_minus_fixed_1_0':out['apts_minus_fixed_1_0'],'utc_bounds':out['symbols']['SOLUSDT']['utc_bounds']},indent=2))
