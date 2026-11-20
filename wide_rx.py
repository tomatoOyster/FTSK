"""Optional hardware-rate CFO search for FTSK-4.

WideReceiver(factor=8, span_hz=20000) accepts contiguous 1.024 MS/s IQ and
covers offsets from -20 to +20 kHz relative to the hardware tuning frequency.
Each independently mixed branch keeps the original narrowband receiver and
its own anti-alias filter. Branches run serially in NumPy. Cost grows with
branch count and FIR length. Measure acquisition throughput on the target
host before live use. Always searching every branch allows packet-to-packet
oscillator changes, including adjacent packets on different offsets.

Packet.start is the estimated header start on the input timeline in units of
one 128 kS/s sample, with RX FIR group delay removed. Multiply by factor for
an estimated hardware sample index (baseband-sample timing resolution).
Packet.cfo_hz includes the digital LO relative to the hardware center.
"""
from collections import deque
from math import ceil, isfinite
from operator import index

import numpy as np

from frame import FRAME_SAMPLES
from radio import Decimator, FS
from receiver import Packet, Receiver, checked_iq


class WideReceiver:
    def __init__(self, factor, span_hz, *, receiver_factory=Receiver, decimator_factory=Decimator):
        self.factor = index(factor)
        span = float(span_hz)
        if self.factor < 1 or not isfinite(span) or span < 0:
            raise ValueError('factor must be positive and span_hz finite and nonnegative')
        rate = FS * self.factor
        if span + 60_000 > rate/2:
            raise ValueError('outer 60 kHz data tone exceeds device Nyquist at requested CFO')
        # Each narrowband bank searches +/-6 kHz. At <=10 kHz spacing every
        # boundary has >=2 kHz overlap. Cover the end points by construction.
        count = max(1, ceil(span / 5000))
        if count > 32:
            raise ValueError('span_hz requires more than 32 parallel acquisition banks')
        self.centers = tuple(span * ((2*j+1)/count - 1) for j in range(count))
        self._branches = [(decimator_factory(self.factor), receiver_factory()) for _ in self.centers]
        self._phase = np.zeros(count)  # cycles modulo one, carried across chunks
        self._increments = np.asarray(self.centers) / rate
        self._delay = 32 if self.factor > 1 else 0
        self._seen = deque()
        self._input_count = 0

    def _block(self, block):
        n = np.arange(len(block), dtype=np.float64)
        candidates = []
        for i, (converter, receiver) in enumerate(self._branches):
            inc = self._increments[i]
            if inc:
                rotator = np.exp(-2j*np.pi*(self._phase[i] + inc*n)).astype(np.complex64)
                baseband = converter.feed(block * rotator)
                self._phase[i] = (self._phase[i] + inc*len(block)) % 1.0
            else:
                baseband = converter.feed(block)
            for p in receiver.feed(baseband):
                packet = Packet(p.start - self._delay, self.centers[i] + p.cfo_hz, p.payload)
                candidates.append((abs(p.cfo_hz), packet))
        self._input_count += len(block)
        # Hold keys across feeds too: two estimates can differ by one sample
        # and so their decoders can complete them in different calls.
        cutoff = self._input_count//self.factor - 2*FRAME_SAMPLES - self._delay
        while self._seen and self._seen[0].start < cutoff:
            self._seen.popleft()
        accepted = []
        for _, pkt in sorted(candidates, key=lambda item: item[0]):
            if any(abs(old.start-pkt.start) <= 8 and abs(old.cfo_hz-pkt.cfo_hz) <= 250
                   and old.payload == pkt.payload for old in self._seen):
                continue
            accepted.append(pkt)
            self._seen.append(pkt)
        return sorted(accepted, key=lambda p: p.start)

    def feed(self, device_samples) -> list[Packet]:
        """Feed contiguous 1-D IQ. Reset after a device sample discontinuity."""
        samples = checked_iq(device_samples, self._input_count, dtype=np.complex64)
        out = []
        size = 1024*self.factor
        for i in range(0, len(samples), size):
            out.extend(self._block(samples[i:i+size]))
        return out
