"""Exercise bounded native stream acquisition and compare same-input Python reference."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
sys.dont_write_bytecode = True
from frame import encode_frame, HEADER, pack_symbols
from ftsk_demo import transmit
from receiver import Receiver


def call(exe,path,chunk=4096):
    p = subprocess.run([exe,'blind',path,str(chunk)],capture_output=True,text=True,timeout=90)
    if p.returncode: raise RuntimeError(p.stderr)
    return [json.loads(x) for x in p.stdout.splitlines()]


def sweep(path,exe,snr,frames):
    rng = np.random.default_rng(423)
    truth = {}; data=[]; t=0
    for n in range(frames):
        payload = rng.bytes(32)
        offset = int(rng.integers(0,850))
        cfo = (-6000,6000,-5937.5,4062.5,0,-1877)[n%6]
        iq = np.pad(encode_frame(payload),(offset,0))
        iq = iq*np.exp(1j*(rng.uniform(0,6.28)+2*np.pi*cfo*np.arange(len(iq))/128000))
        iq += 10**(-snr/20)/np.sqrt(2)*(rng.normal(size=len(iq))+1j*rng.normal(size=len(iq)))
        truth[payload.hex()] = (t+offset,cfo)
        t += len(iq);data.append(iq)
    stream = np.concatenate(data).astype('<c8');stream.tofile(path)
    native = call(exe,path,311)
    py = Receiver(); reference=[]
    for i in range(0,len(stream),1021): reference += py.feed(stream[i:i+1021])
    for p in native:
        start,cfo = truth[p['payload_hex']]
        assert abs(p['start']-start)<=1 and abs(p['cfo_hz']-cfo)<60,p
    assert len(set(p['payload_hex'] for p in native))==len(native)
    if snr==-3:
        assert len(native)==len(reference)==frames
    print(f'SNR={snr}: native={len(native)}/{frames}, Python={len(reference)}/{frames}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path);args=parser.parse_args();exe=args.exe.resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as d:
        path=Path(d)/'stream.cf32'
        for snr in (-8,-6,-3): sweep(path,exe,snr,36)
        payload = bytes(range(32));good = encode_frame(payload)
        symbols=pack_symbols(payload);symbols[5]^=1
        bad=np.r_[HEADER,transmit(symbols).ravel()]
        np.r_[np.zeros(319),bad,good,good].astype('<c8').tofile(path)
        for chunk in (23,257,4096,65536):
            pkts=call(exe,path,chunk)
            assert [(p['start'],p['payload_hex']) for p in pkts]==[(4799,payload.hex()),(9279,payload.hex())],pkts
        rng=np.random.default_rng(148)
        n=128000*4
        (rng.normal(size=n)+1j*rng.normal(size=n)).astype('<c8').tofile(path)
        before=time.perf_counter();assert not call(exe,path);elapsed=time.perf_counter()-before
        print(f'4 s pure complex Gaussian noise, no packets; {elapsed:.3f}s wall, {4/elapsed:.2f}x realtime (file I/O included)')
        with path.open('ab') as f:f.write(b'!')
        result=subprocess.run([exe,'blind',path],capture_output=True,text=True)
        assert result.returncode==1 and 'truncated' in result.stderr
        print('CRC rejection, adjacent repeated payloads, EOF, timestamps, chunk sizes and truncated IQ passed')


if __name__=='__main__': main()
