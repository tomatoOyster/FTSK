"""Continuous, unpaced blind-receiver soak test. Writes auditable JSONL checkpoints."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np
from frame import FS, FRAME_SAMPLES, encode_frame
from receiver import Receiver

# sample SNR dB, clock ppm, echo amplitude at delay 3, impulsive slot, noise-only
CASES = {'nominal': (0, 0, 0, False, False), 'edge': (-6, 0, 0, False, False),
         'clock_echo': (-3, 25, .25, False, False), 'impulse': (0, 0, 0, True, False),
         'noise': (0, 0, 0, False, True)}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def run(seconds, seed, log):
    source = Path(__file__).resolve().parent
    hashes = {name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['frame.py', 'receiver.py', 'ftsk_demo.py', 'soak.py']}
    rng = np.random.default_rng(seed)
    rx = Receiver()
    stats = {key: Counter() for key in CASES}
    processed = samples = excess = max_buffer = 0
    start = time.monotonic()
    cpu = time.process_time()
    next_record = 60
    with Path(log).open('x', encoding='utf8', buffering=1) as out:
        def record(kind, **extra):
            out.write(json.dumps(dict(kind=kind, utc=stamp(), elapsed_s=round(time.monotonic()-start, 3), cpu_s=round(time.process_time()-cpu, 3), **extra))+'\n')
        record('start', python=platform.python_version(), numpy=np.__version__, seed=seed, requested_s=seconds, sha256=hashes)
        while time.monotonic()-start < seconds:
            key = tuple(CASES)[processed % len(CASES)]
            snr, ppm, echo, impulse, only_noise = CASES[key]
            payload = processed.to_bytes(8, 'big') + rng.bytes(24)
            frame = encode_frame(payload)
            if ppm:
                x = np.arange(int(np.ceil(len(frame)*(1+ppm*1e-6))))/(1+ppm*1e-6)
                frame = np.interp(x, np.arange(len(frame)), frame, right=0)
            if echo:
                frame = np.convolve(frame, [1, 0, 0, 1j*echo])
            if impulse:
                slot = int(rng.integers(16, 140))*32
                frame[slot:slot+32] += 12*np.exp(2j*np.pi*rng.uniform(-.45,.45)*np.arange(32))
            leading = int(rng.integers(0, 512))
            cfo = rng.uniform(-6000,6000)
            s = np.pad(frame*np.exp(1j*(rng.uniform(-np.pi,np.pi)+2*np.pi*cfo*np.arange(len(frame))/FS)),(leading,128))
            if only_noise:
                s[:] = 0
            std = np.sqrt(.5*10**(-snr/10))
            s += std*(rng.normal(size=len(s))+1j*rng.normal(size=len(s)))
            packets = []
            i = 0
            while i < len(s):
                n = int(rng.integers(128, 1300))
                packets.extend(rx.feed(s[i:i+n]))
                i += n
            matches = [p for p in packets if not only_noise and p.payload == payload]
            stat = stats[key]
            stat['windows'] += 1
            stat['samples'] += len(s)
            stat['ok'] += bool(matches)
            stat['unexpected'] += len(packets)-bool(matches)
            excess += len(packets)-bool(matches)
            if matches:
                stat['max_start_error_samples'] = max(stat['max_start_error_samples'],abs(matches[0].start-leading-samples))
                stat['max_cfo_error_hz'] = round(max(stat['max_cfo_error_hz'],abs(matches[0].cfo_hz-cfo)),3)
            samples += len(s)
            processed += 1
            max_buffer = max(max_buffer,len(rx._iq))
            if time.monotonic()-start >= next_record:
                record('progress', windows=processed, samples=samples, receiver_buffer_highwater_at_window_end=max_buffer, cases=stats)
                next_record += 60
        record('finish', windows=processed, samples=samples, signal_seconds=samples/FS, cases=stats, unexpected=excess, receiver_buffer_highwater_at_window_end=max_buffer)
        print(f'{processed} windows, {samples} samples, {excess} unexpected packets; {log}')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=int, default=3000)
    p.add_argument('--seed', type=int, default=90517)
    p.add_argument('--log', required=True)
    a = p.parse_args()
    if a.seconds < 1:
        p.error('seconds must be positive')
    run(a.seconds, a.seed, a.log)
