import unittest
from time import perf_counter

import numpy as np
from frame import FRAME_SAMPLES, encode_frame
from radio import FS, interpolate
from wide_rx import WideReceiver


class WideTests(unittest.TestCase):
    def test_coverage_and_validation(self):
        rx = WideReceiver(8, 20000)
        self.assertEqual(rx.centers, (-15000, -5000, 5000, 15000))
        for offset in np.arange(-20000, 20001, 125):
            self.assertLessEqual(min(abs(offset-c) for c in rx.centers), 5000)
        for factor, span in [(0, 1), (1, 20000), (8, -1), (32, 500000), (8, np.nan)]:
            with self.assertRaises(ValueError):
                WideReceiver(factor, span)

    def test_edges_overlap_and_adjacent_packets(self):
        rng = np.random.default_rng(17)
        frame = encode_frame(bytes(range(32)))
        factor = 8
        # Contiguous frames including three identical payloads at the exact LO crossover.
        offsets = [-20000, -18300, -10000, -10000, -10000, 0, 10000, 18300, 20000]
        burst = interpolate(np.tile(frame, len(offsets)), factor)
        origin = 32*factor   # TX reconstruction latency in input device samples
        indices = np.clip((np.arange(len(burst))-origin)//(FRAME_SAMPLES*factor), 0, len(offsets)-1)
        cycles = np.cumsum(np.asarray(offsets)[indices])/(factor*FS)
        stream = np.pad(burst*np.exp(2j*np.pi*cycles), (45*factor+3, 600*factor))
        stream += 0.07*(rng.normal(size=len(stream)) + 1j*rng.normal(size=len(stream)))
        rx = WideReceiver(factor, 20000)
        packets = []
        start = 0
        while start < len(stream):
            step = int(rng.integers(37, 12000))
            packets.extend(rx.feed(stream[start:start+step]))
            start += step
        self.assertEqual([p.payload for p in packets], [bytes(range(32))]*len(offsets))
        for i,p in enumerate(packets):
            self.assertLessEqual(abs(p.start - (45+32+i*FRAME_SAMPLES)), 1)
            self.assertLess(abs(p.cfo_hz-offsets[i]), 30)

    def test_noise(self):
        rx = WideReceiver(8, 20000)
        rng = np.random.default_rng(81)
        n = 18000*8
        self.assertFalse(rx.feed(rng.normal(size=n)+1j*rng.normal(size=n)))


def benchmark():
    rate = 8*FS
    rng = np.random.default_rng(78)
    stream = (rng.normal(size=rate)+1j*rng.normal(size=rate)).astype(np.complex64)
    for span in (0, 20000):
        rx = WideReceiver(8, span)
        start = perf_counter()
        for i in range(0,len(stream),8192):
            rx.feed(stream[i:i+8192])
        elapsed = perf_counter()-start
        print(f'{len(rx.centers)} banks: {elapsed:.3f}s per 1s capture, {1/elapsed:.2f}x realtime')


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--benchmark']:
        benchmark()
    else:
        unittest.main()
