"""Paired acquisition validation and benchmark. Run with --benchmark for timing."""
import unittest
from time import perf_counter
import numpy as np
from fast_acquire import FastReceiver, FFTDecimator
from frame import FS, encode_frame
from receiver import Receiver
from wide_rx import WideReceiver
from radio import interpolate, Decimator


def run(rx, data, chunk):
    packets = []
    for i in range(0, len(data), chunk):
        packets.extend(rx.feed(data[i:i+chunk]))
    return packets


def paired(count, snr, seed):
    rng = np.random.default_rng(seed)
    counts = [0, 0, 0, 0]  # neither, fast only, exhaustive only, both
    for i in range(count):
        payload = rng.bytes(32)
        cfo = (-6000, -5937.5, 0, 4062.5, 5937.5, 6000)[i%6]
        offset = int(rng.integers(1,1800))
        iq = np.pad(encode_frame(payload), (offset, 200))
        iq = iq*np.exp(1j*(rng.uniform(0,6.28)+2*np.pi*cfo*np.arange(len(iq))/FS))
        iq += 10**(-snr/20)/np.sqrt(2)*(rng.normal(size=len(iq))+1j*rng.normal(size=len(iq)))
        key = 0
        for rx, weight in [(Receiver(),2),(FastReceiver(),1)]:
            got = run(rx,iq, (43,541,4096)[i%3])
            if got:
                assert len(got)==1 and got[0].payload==payload
                assert abs(got[0].start-offset)<=1 and abs(got[0].cfo_hz-cfo)<50
                key += weight
        counts[key] += 1
    return counts


class FastTests(unittest.TestCase):
    def test_paired_capture(self):
        counts = paired(24,-3,2029)
        self.assertEqual(counts, [0,0,0,24])

    def test_false_gates(self):
        rng = np.random.default_rng(6)
        rx = FastReceiver()
        for _ in range(4):
            n = FS//4
            self.assertFalse(run(rx,rng.normal(size=n)+1j*rng.normal(size=n), 517))
        self.assertLess(rx.prescreen_starts, rx.tested_starts//100)
        self.assertEqual(rx.header_candidates,0)

    def test_fft_decimation(self):
        rng = np.random.default_rng(417)
        for factor in (1, 2, 3, 8):
            direct, fast = Decimator(factor), FFTDecimator(factor)
            for count in (0, 1, 2, 37, 512, 1023, 4097, 9):
                data = (rng.normal(size=count)+1j*rng.normal(size=count)).astype(np.complex64)
                np.testing.assert_allclose(fast.feed(data), direct.feed(data), atol=3e-6, rtol=3e-6)

    def test_wide_edges(self):
        rx = WideReceiver(8, 20000, receiver_factory=FastReceiver, decimator_factory=FFTDecimator)
        p = bytes(range(32))
        iq = interpolate(np.pad(encode_frame(p),(127,255)),8)
        for cfo in (-20000, -18300, -10000, 18300, 20000):
            data = iq*np.exp(2j*np.pi*cfo*np.arange(len(iq))/(8*FS))
            packets = run(rx,data,4079)
            self.assertEqual([z.payload for z in packets],[p])
            self.assertLess(abs(packets[0].cfo_hz-cfo),20)


def benchmark():
    rng = np.random.default_rng(13)
    for size in (1,8):
        n = FS*size
        noise = (rng.normal(size=n)+1j*rng.normal(size=n)).astype(np.complex64)
        for factory in (Receiver,FastReceiver):
            rx = factory() if size==1 else WideReceiver(8,20000,receiver_factory=factory,
                    decimator_factory=FFTDecimator if factory is FastReceiver else Decimator)
            start = perf_counter()
            packets = run(rx, noise, 1024*size)
            elapsed = perf_counter()-start
            print(f'{factory.__name__} {1 if size==1 else 4} bank(s): {elapsed:.3f} s/1s capture, {1/elapsed:.2f}x realtime, {len(packets)} packets',flush=True)
            if size==1 and isinstance(rx,FastReceiver):
                print(f'Noise prescreen {rx.prescreen_starts}/{rx.tested_starts}, coherent candidates {rx.header_candidates}',flush=True)
    for snr in (-8,-6,-3):
        print(f'{snr}dB, [neither,fast only,exhaustive only,both]: {paired(60,snr,49)}', flush=True)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--benchmark']:
        benchmark()
    else:
        unittest.main()
