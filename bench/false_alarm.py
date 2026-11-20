"""Count raw header candidates and CRC-passing deliveries on noise-only IQ."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from frame import FS
from fast_acquire import FastReceiver


def measure(seconds, seed, mode):
    rng = np.random.default_rng(seed)
    rx = FastReceiver()
    total = int(seconds*FS)
    false = 0
    first = []
    wall = time.perf_counter()
    for i in range(0, total, 8192):
        count = min(8192,total-i)
        x = (rng.normal(size=count)+1j*rng.normal(size=count))/np.sqrt(2)
        if mode=='cw':
            x += 2*np.exp(2j*np.pi*12345*(np.arange(count)+i)/FS)
        elif mode=='impulsive':
            mask = rng.random(size=count) < .0002
            x[mask] *= 30
        packets = rx.feed(x)
        false += len(packets)
        if len(first)<8:
            first.extend(p.start for p in packets[:8-len(first)])
    return dict(mode=mode, seed=seed, input_s=total/FS, wall_s=time.perf_counter()-wall,
                samples=total, timings_examined=rx.tested_starts, prescreen_hits=int(rx.prescreen_starts),
                candidate_headers=rx.header_candidates, crc_passing_packets=false, earliest_false=first,
                zero_count_poisson_upper_95_per_hour=(-math.log(.05)*3600/seconds if not false else None),
                sha256={k:hashlib.sha256((ROOT/k).read_bytes()).hexdigest() for k in ['fast_acquire.py','frame.py','receiver.py']})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=int, default=3600)
    p.add_argument('--mode',choices=['awgn','cw','impulsive'],default='awgn')
    p.add_argument('--seed',type=int,default=88012)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.seconds<1:
        p.error('seconds must be positive')
    result=measure(a.seconds,a.seed,a.mode)
    with a.out.open('x',encoding='utf8') as f:
        json.dump(result,f,indent=2)
    print(json.dumps(result))
