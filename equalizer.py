"""Optional static-channel-aware matched equalizer for FTSK-4 v1 frames."""
import argparse
import json
from pathlib import Path

import numpy as np

from frame import FRAME_SAMPLES, FS, HEADER, _checked_payload
from ftsk_demo import SYMBOLS, transmit
from receiver import Packet, Receiver

# A symmetric impulse response allows acquisition on the stronger of two paths.
RADIUS = 12
LAGS = np.arange(-RADIUS, RADIUS + 1)
HEADER_MIDDLE = np.arange(RADIUS, len(HEADER) - RADIUS)
TRAIN = HEADER[HEADER_MIDDLE[:, None] - LAGS].astype(np.complex128)
GRAM = TRAIN.conj().T @ TRAIN
FIT = np.linalg.solve(GRAM + .04 * len(TRAIN) * np.eye(len(LAGS)), TRAIN.conj().T)
SYMBOL_WAVE = transmit(SYMBOLS).reshape(512, 128).astype(np.complex64)
SAMPLES = np.arange(FRAME_SAMPLES)


def retry(raw, coarse):
    """Fit one ridge-regularized channel and verify one payload hypothesis."""
    # Use the same fine-CFO search span as the baseline receiver.
    dechirped = raw[:len(HEADER)] * HEADER.conj() * np.exp(-2j*np.pi*coarse*SAMPLES[:len(HEADER)]/FS)
    offsets = np.arange(-32, 33)
    peak = offsets[np.abs(np.fft.fft(dechirped, n=32768)[offsets]).argmax()]
    freq = coarse + peak*FS/32768
    corrected = raw*np.exp(-2j*np.pi*freq*SAMPLES/FS)
    training = corrected[HEADER_MIDDLE]
    taps = FIT @ training
    residual = training - TRAIN @ taps
    if np.vdot(residual, residual).real > .90 * np.vdot(training, training).real:
        return None
    # Implement the matched equalizer in the forward channel domain. With a
    # free common phase per symbol, its likelihood is 2|<y,Hx>| - ||Hx||².
    # Crop symbol edges so the neighboring data is not an unknown predictor.
    noise_power = np.mean(np.abs(residual)**2)
    variance = noise_power * np.sum(np.abs(FIT)**2, axis=1)
    taps *= np.maximum(0, 1 - 2*variance / np.maximum(np.abs(taps)**2, 1e-20))
    keep = np.abs(taps) >= .14 * np.max(np.abs(taps))
    lags, taps = LAGS[keep], taps[keep]
    left = max(0, int(max(lags)))
    right = 128 + min(0, int(min(lags)))
    templates = np.zeros((512, right-left), dtype=np.complex64)
    for lag, tap in zip(lags, taps):
        templates += tap * SYMBOL_WAVE[:, left-lag:right-lag]
    body = corrected[len(HEADER):].reshape(31, 128)[:, left:right]
    cross = np.einsum('bt,kt->bk', body.astype(np.complex64), templates.conj(), optimize=False)
    energy = np.sum(np.abs(templates)**2, axis=1)
    provisional = (2*np.abs(cross) - energy).argmax(axis=1)
    phasors = cross[np.arange(len(body)), provisional]
    times = (len(HEADER) + 128*np.arange(len(body)) + 64)/FS
    offsets = np.arange(-80,81,2)
    rotations = np.exp(-2j*np.pi*offsets[:, None]*times)
    totals = rotations @ phasors
    best = np.abs(totals).argmax()
    coherent = (cross * (rotations[best]*np.exp(-1j*np.angle(totals[best])))[:,None]).real
    scores = 2*coherent - energy
    payload = _checked_payload(scores.argmax(axis=1))
    return (freq, payload) if payload is not None else None


class EqualizedReceiver(Receiver):
    def _decode(self, start, coarse):
        packet = super()._decode(start, coarse)
        if packet is not None:
            return packet
        offset = start - self._base
        result = retry(self._iq[offset:offset+FRAME_SAMPLES], coarse)
        return Packet(start, *result) if result is not None else None


def benchmark(count, seed):
    from channels.study import CASES, capture, noise_and_reference
    for name in ('base', 'echo2', 'echo20', 'echo80', 'echo250'):
        row = dict(case=name, trials=count, baseline=0, enhanced=0, gained=0, lost=0, wrong=0)
        for index in range(count):
            ref = noise_and_reference(index, seed, 8)
            iq = capture(ref, CASES[name], -3, 8)
            results = []
            for receiver in (Receiver(), EqualizedReceiver()):
                packets = []
                for pos in range(0, len(iq), 731):
                    packets.extend(receiver.feed(iq[pos:pos+731]))
                results.append([p.payload for p in packets])
            base_ok, enhanced_ok = [packets == [ref[0]] for packets in results]
            row['baseline'] += base_ok
            row['enhanced'] += enhanced_ok
            row['gained'] += enhanced_ok and not base_ok
            row['lost'] += base_ok and not enhanced_ok
            row['wrong'] += sum(value != ref[0] for value in results[1])
        print(json.dumps(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True, dest='mode')
    decode = commands.add_parser('decode')
    decode.add_argument('iq', type=Path)
    bench = commands.add_parser('benchmark')
    bench.add_argument('--trials', type=int, default=64)
    bench.add_argument('--seed', type=int, default=194911)
    args = parser.parse_args()
    if args.mode == 'benchmark':
        if args.trials <= 0 or args.seed < 0:
            parser.error('invalid trial count or seed')
        benchmark(args.trials, args.seed)
        return
    if args.iq.stat().st_size % 8:
        parser.error('incomplete CF32 sample')
    receiver = EqualizedReceiver()
    with args.iq.open('rb') as stream:
        while block := stream.read(32768):
            for pkt in receiver.feed(np.frombuffer(block, dtype='<c8')):
                print(json.dumps(dict(start_sample=pkt.start,cfo_hz=pkt.cfo_hz,payload_hex=pkt.payload.hex())))


if __name__ == '__main__':
    main()
