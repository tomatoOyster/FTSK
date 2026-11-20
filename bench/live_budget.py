"""Measure complete calibrated single-branch receive processing in device-sized blocks."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fast_acquire import FFTDecimator, FastReceiver
from frame import encode_frame
from radio import interpolate


def trial(data, expected, chunk, rng):
    rate = 1_024_000
    offset = 18_277.5
    rx = FastReceiver()
    conv = FFTDecimator(8)
    times = []
    cpu = time.process_time()
    start = time.perf_counter()
    payloads = []
    for i in range(0, len(data), chunk):
        before = time.perf_counter()
        x = data[i:i+chunk]
        phase = ((i*offset / rate) % 1 + np.arange(len(x))*offset/rate)
        base = conv.feed(x * np.exp(-2j*np.pi*phase).astype(np.complex64))
        payloads.extend(p.payload for p in rx.feed(base))
        times.append(time.perf_counter()-before)
    elapsed = time.perf_counter()-start
    return dict(cpu_s=time.process_time()-cpu, wall_s=elapsed, throughput_x=(len(data)/rate)/elapsed,
                block_p99_ms=float(np.percentile(times, 99))*1000, max_ms=max(times)*1000,
                packets=len(payloads), all_match=(expected==payloads))


def measure(repeats, seconds, mtu):
    rng = np.random.default_rng(11223)
    frames = int(seconds*1_024_000//(8*(4480+192)))
    expected = [rng.bytes(32) for _ in range(frames)]
    data = np.concatenate([interpolate(np.pad(encode_frame(p),(64,64)),8) for p in expected])
    noise = (.3*(rng.normal(size=len(data))+1j*rng.normal(size=len(data)))).astype(np.complex64)
    data = (data*np.exp(2j*np.pi*18277.5*np.arange(len(data))/1_024_000)+noise).astype(np.complex64)
    output = {}
    for tag, sample, packets in [('noise',noise,[]), ('consecutive_packets',data,expected)]:
        rows = [trial(sample, packets, mtu,rng) for _ in range(repeats)]
        output[tag] = dict(runs=rows, median_throughput_x=float(np.median([r['throughput_x'] for r in rows])),
                           median_block_p99_ms=float(np.median([r['block_p99_ms'] for r in rows])))
    return dict(rate=1_024_000, mtu=mtu, block_duration_ms=1000*mtu/1_024_000, python=platform.python_version(),
                numpy=np.__version__, input_seconds=len(data)/1_024_000, repeats=repeats, simulated=True,
                sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ('fast_acquire.py','radio.py','receiver.py','frame.py')}, results=output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds',type=float,default=2)
    p.add_argument('--repeats',type=int,default=7)
    p.add_argument('--mtu',type=int,default=8192)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.seconds<.1 or a.repeats<1 or a.mtu<1:
        p.error('invalid test dimensions')
    report=json.dumps(measure(a.repeats,a.seconds,a.mtu),indent=2)
    if a.output:
        a.output.write_text(report+'\n',encoding='utf8')
    print(report)
