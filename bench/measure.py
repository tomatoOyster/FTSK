"""Reproducible v1 digital SDR bench. Uses only NumPy and the shipped v1 zip."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE.parent / 'fixtures' / 'ftsk4-v1.zip'
sys.path.insert(0, str(ARCHIVE))  # pin original source independent of working tree edits
import frame
import radio
from receiver import Receiver

RATE = 1024000
L = 8
SEED = 481516


def tx(payload):
    base = frame.encode_frame(payload)
    out = radio.interpolate(np.pad(base, (256, 256)), L)
    out *= 0.8 / max(1., float(np.max(np.abs(out))))
    return base, out


def db(power):
    return 10 * np.log10(power)


def spectral(n):
    rng = np.random.default_rng(SEED)
    accum = [np.zeros(k) for k in (8192, 65536)]  # identical frequency spacing
    ratios = []
    for _ in range(n):
        base, out = tx(rng.bytes(32))
        for y, ps in zip((base, out), accum):
            ps += np.abs(np.fft.fftshift(np.fft.fft(y.astype(np.complex128), n=len(ps)))) ** 2
        active = out[(256+32)*L:(256+32+len(base))*L]
        ratios.append([db(np.max(abs(y)**2)/np.mean(abs(y)**2)) for y in (base, active, out)])
    result = {'frames':n}
    for tag, ps, rate in zip(('base','device'), accum, (128000, RATE)):
        f = np.fft.fftshift(np.fft.fftfreq(len(ps),1/rate))
        cdf = np.cumsum(ps)/np.sum(ps)
        lo, hi = f[np.searchsorted(cdf,[0.005,0.995])]
        result[tag] = {'lo_hz':lo, 'hi_hz':hi, 'obw99_hz': hi-lo,
                       'outside_64khz_pct': float(100*ps[abs(f)>64000].sum()/ps.sum())}
    result['papr_db_min_median_p95_max'] = dict(zip(('base_active','device_active','device_burst'),
                                                    np.percentile(ratios,[0,50,95,100],axis=0).T.tolist()))
    return result


def channel(n):
    taps = radio.fir(L)
    noise_gain = np.sum(taps.astype(float)**2)
    results = []
    for cfo, ppm, snr, mode in [(0,0,-6,'random'), (6000,0,-6,'random'), (-6000,0,-6,'random'),
                                 (5850,0,-6,'random'), (4000,0,-6,'random'), (6000,0,-3,'random'),
                                 (-6000,0,-3,'zeros'), (-6000,0,15,'zeros'), (6000,0,15,'ff'),
                                 (4575,5,-3,'random'),(9150,10,15,'random'),(18300,20,15,'random')]:
        rng = np.random.default_rng(SEED+1)
        counters = [0,0]
        mismatches = [0,0]  # base-only successes, RF-only successes
        for trial in range(n):
            payload = rng.bytes(32)
            if mode == 'zeros': payload = bytes(32)
            if mode == 'ff': payload = bytes([255])*32
            base, out = tx(payload)
            # Randomize start phase relative to the receiver's decimator and input timeline.
            pad = int(rng.integers(0,800))
            phase = rng.uniform(-np.pi,np.pi)
            out = np.pad(out,(pad,0))
            # A positive ppm stretches the received waveform by the stated ratio.
            if ppm:
                t = np.arange(round(len(out)*(1+ppm*1e-6)))/(1+ppm*1e-6)
                out = np.interp(t, np.arange(len(out)), out, right=0)
            scale = np.sqrt(np.mean(abs(out[(256+32)*L+pad:(256+32+4480)*L+pad])**2))
            out = out / scale * np.exp(1j*(phase+2*np.pi*cfo*np.arange(len(out))/RATE))
            sigma = 10**(-snr/20)/np.sqrt(2*noise_gain)
            noise = sigma*(rng.normal(size=len(out))+1j*rng.normal(size=len(out)))
            device = radio.Decimator(L).feed((out+noise).astype('c8'))
            # Base control uses the same underlying filtered noise, integer timing, same CFO/SFO.
            clean = np.pad(base, (256+64+round(pad/L),256))
            if ppm:
                t = np.arange(round(len(clean)*(1+ppm*1e-6)))/(1+ppm*1e-6)
                clean = np.interp(t,np.arange(len(clean)),clean,right=0)
            clean = clean * np.exp(1j*(phase+2*np.pi*cfo*np.arange(len(clean))/128000))
            control = radio.Decimator(L).feed(noise.astype('c8'))
            control[:min(len(clean),len(control))] += clean[:len(control)]
            ok = [any(p.payload==payload for p in Receiver().feed(y)) for y in (control,device)]
            counters = [x+y for x,y in zip(counters,ok)]
            mismatches[0] += ok[0] and not ok[1]
            mismatches[1] += ok[1] and not ok[0]
        results.append(dict(cfo_hz=cfo, clock_ppm=ppm, snr_db=snr, payload=mode, trials=n,
                            base_ok=counters[0],device_ok=counters[1],base_only=mismatches[0],device_only=mismatches[1]))
    guard = {}
    for hz in [60000,62000,64000,65000,66000,68000]:
        resp = sum(taps*np.exp(-2j*np.pi*hz*np.arange(len(taps))/RATE))
        guard[str(hz)] = round(float(db(abs(resp)**2)),3)
    return {'fir_gain_db':guard,'conditions':results}


def timing():
    rng = np.random.default_rng(SEED+2)
    samples = (rng.normal(size=RATE)+1j*rng.normal(size=RATE)).astype('c8')
    dense = np.tile(frame.encode_frame(rng.bytes(32)),30)[:128000]
    cases = {}
    for name in ('noise_rx','dense_rx','decimator','both_noise'):
        times, cpus, p99s, maxes = [], [], [], []
        for rep in range(8):
            dec = radio.Decimator(L)
            rx = Receiver()
            y = samples if name in ('decimator','both_noise') else (dense if name=='dense_rx' else samples[:128000])
            step = 8192 if len(y)==RATE else 1024
            latencies=[]
            c0=time.process_time(); w0=time.perf_counter()
            for start in range(0,len(y),step):
                t=time.perf_counter(); chunk=y[start:start+step]
                if name in ('both_noise','decimator'): chunk=dec.feed(chunk)
                if name!='decimator': rx.feed(chunk)
                latencies.append((time.perf_counter()-t)*1000)
            wall=time.perf_counter()-w0; cpu=time.process_time()-c0
            if rep:
                times.append(wall); cpus.append(cpu); p99s.append(np.percentile(latencies,99)); maxes.append(max(latencies))
        med = np.median(times)
        cases[name] = {'wall_s_per_iq_second_min_med_max': [min(times),med,max(times)],
                       'process_cpu_s_per_iq_second_median':np.median(cpus),
                       'samples_per_wall_second':int(len(y)/med),'realtime_x':1/med,
                       'chunk_p99_ms_median':np.median(p99s),'worst_chunk_ms':max(maxes)}
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('part',choices=['spectral','channel','timing'])
    p.add_argument('--n',type=int)
    a=p.parse_args()
    if a.n is not None and a.n<1: p.error('--n must be positive')
    if a.part=='spectral': data=spectral(a.n or 512)
    elif a.part=='channel': data=channel(a.n or 96)
    else: data=timing()
    result={'schema':1,'seed':SEED,'zip_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
            'python':platform.python_version(),'numpy':np.__version__,'platform':platform.platform(),
            'cpu':platform.processor(),'logical_cpus':os.cpu_count(),a.part:data}
    (HERE/(a.part+'.json')).write_text(json.dumps(result,indent=2), encoding='utf-8')

if __name__=='__main__': main()
