"""Blind packet waterfall, common random numbers across SNR levels; counts include missed headers."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from frame import FS, encode_frame
from fast_acquire import FastReceiver
from receiver import Receiver
from linktest import confidence


def run(trials, levels, seed):
    # Independent packet draws. Both receivers see bit-identical streams at each SNR.
    source_hash={n:hashlib.sha256((BASE/n).read_bytes()).hexdigest() for n in ('frame.py','receiver.py','fast_acquire.py','ftsk_demo.py')}
    rng=np.random.default_rng(seed)
    stats={str(level): {k:{'ok':0,'wrong_or_duplicate':0,'header_hit_payload_mismatch':0} for k in ['exhaustive','fast']} for level in levels}
    pairs={str(level):{'only_exhaustive':0,'only_fast':0} for level in levels}
    start=time.monotonic()
    for _ in range(trials):
        payload=rng.bytes(32)
        lead=int(rng.integers(0, 1024))
        carrier=rng.uniform(-6000,6000)
        phase=rng.uniform(-np.pi,np.pi)
        wave=np.pad(encode_frame(payload), (lead,128))
        wave=wave*np.exp(1j*(phase+2*np.pi*carrier*np.arange(len(wave))/FS))
        noise=(rng.normal(size=len(wave))+1j*rng.normal(size=len(wave)))/np.sqrt(2)
        for level in levels:
            x=wave+noise*10**(-level/20)
            decoded=[]
            for tag,cls in [('exhaustive',Receiver),('fast',FastReceiver)]:
                rx=cls()
                packets=rx.feed(x)
                correct=any(p.payload==payload for p in packets)
                row=stats[str(level)][tag]
                row['ok']+=int(correct)
                row['wrong_or_duplicate']+=len(packets)-int(correct)
                row['header_hit_payload_mismatch']+=sum(p.start==lead and p.payload!=payload for p in packets)
                decoded.append(correct)
            pairs[str(level)]['only_exhaustive']+=int(decoded==[True,False])
            pairs[str(level)]['only_fast']+=int(decoded==[False,True])
    for level in levels:
        for row in stats[str(level)].values():
            errors=trials-row['ok']
            row.update(trials=trials,errors=errors,per=errors/trials,wilson95=confidence(errors,trials))
    return dict(trials_per_level=trials, snr_db_per_complex_sample=levels, seed=seed, seconds=time.monotonic()-start,
                sha256=source_hash,
                paired_disagreements=pairs, results=stats)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trials',type=int,default=1200)
    p.add_argument('--levels',nargs='+',type=float,default=[-9,-8,-7,-6,-5,-4])
    p.add_argument('--seed',type=int,default=9020)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.trials<1 or len(set(a.levels))!=len(a.levels) or not np.all(np.isfinite(a.levels)):
        p.error('require positive trials and unique finite levels')
    result=run(a.trials,a.levels,a.seed)
    with a.out.open('x',encoding='utf8') as f:
        json.dump(result,f,indent=2)
    print(json.dumps(result))
