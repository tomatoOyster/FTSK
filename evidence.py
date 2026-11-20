"""Check duration, sample accounting, source fingerprints and progress of a soak JSONL log."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from linktest import confidence


def check(path, minimum=0):
    rows = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line]
    if len(rows)<2 or rows[0]['kind']!='start' or rows[-1]['kind']!='finish':
        raise ValueError('no complete start/finish record')
    start,end=rows[0],rows[-1]
    prev=None
    for row in rows[1:]:
        counts=row['cases']
        if sum(c['samples'] for c in counts.values())!=row['samples'] or sum(c['windows'] for c in counts.values())!=row['windows']:
            raise ValueError('sample/window accounting mismatch')
        if prev and (row['elapsed_s']<prev['elapsed_s'] or row['samples']<prev['samples'] or row['cpu_s']<prev['cpu_s']):
            raise ValueError('non-monotone progress counters')
        if any(c['ok']>c['windows'] or c['unexpected']<0 for c in counts.values()):
            raise ValueError('invalid packet counters')
        prev=row
    wall=(datetime.fromisoformat(end['utc'])-datetime.fromisoformat(start['utc'])).total_seconds()
    if abs(wall-end['elapsed_s'])>5:
        raise ValueError('wall and monotonic clocks differed by >5 s')
    if end['elapsed_s'] < max(minimum,start['requested_s']):
        raise ValueError('log does not meet required duration')
    result={}
    for name,c in end['cases'].items():
        if name=='noise':
            result[name]={'input_seconds':c['samples']/128000, 'unexpected':c['unexpected']}
        else:
            lost=c['windows']-c['ok']
            result[name]={'received':c['ok'], 'sent':c['windows'], 'lost':lost, 'unexpected':c['unexpected'],
                          'per':lost/c['windows'], 'per_wilson95':confidence(lost,c['windows'])}
    return dict(log=path.name,utc_start=start['utc'],utc_end=end['utc'],wall_s=wall,monotonic_s=end['elapsed_s'],
                cpu_s=end['cpu_s'],input_seconds=end['signal_seconds'],windows=end['windows'],samples=end['samples'],
                source_at_start=start['sha256'],stats=result)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('log',type=Path)
    p.add_argument('--minimum',type=int,default=0)
    a=p.parse_args()
    print(json.dumps(check(a.log,a.minimum),indent=2))
