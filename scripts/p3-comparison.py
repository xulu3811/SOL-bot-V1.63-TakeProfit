from pathlib import Path
import argparse,json
from solbot.mathematics.comparison import synthetic_study
parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
args=parser.parse_args()
result=synthetic_study()
Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'scenarios':len(result),'repeat_equal':result==synthetic_study(),
                 'net_pnl':{r['name']:{k:v['net_pnl'] for k,v in r['models'].items()} for r in result}}))
