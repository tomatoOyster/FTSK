"""Paired pre-FIR LO experiments, same noisy input for all three receivers."""
from pathlib import Path
import hashlib
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import frame, radio, receiver, wide_rx
RATE = 1024000
FACTOR = 8


def run(n=24):
    payload = bytes(32)
    tx = radio.interpolate(np.pad(frame.encode_frame(payload), (256, 256)), FACTOR)
    tx *= .8 / max(1., float(np.max(np.abs(tx))))
    rms = np.sqrt(np.mean(abs(tx[288*8:(288+4480)*8])**2))
    sigma = 10**(3/20) / np.sqrt(2*np.sum(radio.fir(FACTOR).astype(float)**2))
    result = []
    for offset in (-6000,6000,18300):
        rng=np.random.default_rng(481520)
        counts = np.zeros(3,int)
        duplicates = np.zeros(3,int)
        for _ in range(n):
            signal = np.pad(tx/rms, (int(rng.integers(0,800)),0))
            phase = rng.uniform(-np.pi,np.pi)
            rot = np.exp(2j*np.pi*offset*np.arange(len(signal))/RATE)
            device=(signal*rot*np.exp(1j*phase)+sigma*(rng.normal(size=len(signal))+1j*rng.normal(size=len(signal)))).astype('c8')
            outputs=[receiver.Receiver().feed(radio.Decimator(8).feed(device)),
                     receiver.Receiver().feed(radio.Decimator(8).feed((device*rot.conj()).astype('c8'))),
                     wide_rx.WideReceiver(8,20000).feed(device)]
            for idx, packets in enumerate(outputs):
                accepted=sum(p.payload==payload for p in packets)
                counts[idx] += accepted>0
                duplicates[idx] += max(accepted-1,0)
        result.append(dict(cfo_hz=offset,n=n,snr_db=-3,ok_narrow_oracle_wide=counts.tolist(),duplicates=duplicates.tolist()))
    source={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['frame.py','radio.py','receiver.py','wide_rx.py']}
    return {'seed':481520,'sha256':source,'sample_clock_ppm':0,'payload':'00'*32,'conditions':result}

if __name__=='__main__':
    (ROOT/'bench'/'correction.json').write_text(json.dumps(run(),indent=2),encoding='utf-8')
