"""Optional dual-profile packet modem. `python profiles.py --help`."""
import argparse
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
import frame
from io_helpers import atomic_writer, check_distinct
from receiver import FS, _H, _NFFT, _GRID, _BLOCK, _RADIUS, _FILTERS, checked_iq

N = frame.N
ABSCISSAS = (0, 1, 3, 7, 12, 18, 23, 29)
SLOW_HEADER_TONES = (21, 8, 26, 11, 1, 24, 6, 18, 28, 12, 4, 23, 9, 20, 2, 16)
TONE = np.exp(2j * np.pi * (np.arange(31)[:, None] - 15) * np.arange(N) / N).astype(np.complex64)
HEADERS = {4: frame.HEADER, 8: TONE[list(SLOW_HEADER_TONES)].ravel()}
LENGTH = {4: frame.FRAME_SAMPLES, 8: _H + 31 * 8 * N}
SYMBOL = np.arange(512)[:, None]
BOOK8 = (SYMBOL % 31 + SYMBOL // 31 * ABSCISSAS) % 31


def encode(payload, profile=4):
    if profile == 4:
        return frame.encode_frame(payload)
    if profile != 8:
        raise ValueError('profile must be 4 or 8')
    return np.concatenate((HEADERS[8], TONE[BOOK8[frame.pack_symbols(payload)]].ravel()))


def _decode8(iq):
    energy = frame.energies(iq.reshape(31, 8, N))
    for attempt in range(2):
        scores = sum(energy[:, slot, BOOK8[:, slot]] for slot in range(8))
        payload = frame._checked_payload(scores.argmax(axis=1))
        if payload is not None:
            return payload
        if attempt == 0:
            energy = energy / np.maximum(energy.sum(axis=-1, keepdims=True), 1e-20)
    return None


@lru_cache(maxsize=1)
def _slow_filters():
    shifted = HEADERS[8] * np.exp((2j*np.pi/FS) * _GRID[:, None] * np.arange(_H))
    return np.fft.fft(shifted[:, ::-1].conj(), n=_NFFT, axis=1)


@dataclass(frozen=True)
class Packet:
    start: int
    profile: int
    cfo_hz: float
    payload: bytes


class DualReceiver:
    """One streaming time axis and shared forward FFT. Candidates carry profile ID."""
    def __init__(self, accepted_profiles=(4, 8)):
        self.banks = [(p, _FILTERS if p == 4 else _slow_filters()) for p in accepted_profiles if p in LENGTH]
        if not self.banks or len(self.banks) != len(accepted_profiles):
            raise ValueError('expected one or both of profiles 4 and 8')
        self.iq = np.empty(0, dtype=np.complex128)
        self.base = self.cursor = self.accepted_end = 0
        self.pending = []

    def _scan(self):
        first = max(0, self.cursor - _RADIUS)
        count = self.cursor + _BLOCK + _RADIUS - first
        off = first - self.base
        data = self.iq[off:off + count + _H - 1]
        spectrum = np.fft.fft(data, n=_NFFT)
        cum = np.r_[0., np.cumsum(abs(data)**2)]
        norm = _H * (cum[_H:] - cum[:-_H])
        for profile, filters in self.banks:
            corr = np.fft.ifft(filters * spectrum, axis=1)[:, _H-1:_H-1+count]
            power = abs(corr)**2
            bins = power.argmax(axis=0)
            best = power[bins, np.arange(count)]
            score = np.divide(best, norm, out=np.zeros(count), where=norm > 0)
            begin = self.cursor - first
            for i in np.flatnonzero(score[begin:begin + _BLOCK] >= 0.055) + begin:
                left = max(0, i - _RADIUS)
                if i == left + np.argmax(score[left:i + _RADIUS + 1]):
                    self.pending.append((first + int(i), -float(score[i]), profile, _GRID[bins[i]]))
        self.cursor += _BLOCK

    def _attempt(self, start, profile, coarse):
        offset = start - self.base
        raw = self.iq[offset:offset+LENGTH[profile]]
        residual = raw[:_H] * HEADERS[profile].conj() * np.exp(-2j*np.pi*coarse*np.arange(_H)/FS)
        bins = np.arange(-32, 33)
        peak = bins[np.argmax(abs(np.fft.fft(residual, n=32768)[bins]))]
        freq = float(coarse + peak*FS/32768)
        body = raw[_H:] * np.exp(-2j*np.pi*freq*np.arange(len(raw)-_H)/FS)
        payload = frame.decode_body(body) if profile == 4 else _decode8(body)
        if payload is not None:
            return Packet(start, profile, freq, payload)
        return None

    def feed(self, iq):
        chunk = checked_iq(iq, self.base + len(self.iq))
        self.iq = np.concatenate((self.iq, chunk))
        end = self.base + len(self.iq)
        while self.cursor + _BLOCK + _RADIUS + _H - 1 <= end:
            self._scan()
        packets, waiting = [], []
        for candidate in sorted(self.pending):
            start, _, profile, coarse = candidate
            if start < self.accepted_end:
                continue
            if start + LENGTH[profile] > end:
                waiting.append(candidate)
                continue
            packet = self._attempt(start, profile, coarse)
            if packet is not None:
                packets.append(packet)
                self.accepted_end = start + LENGTH[profile]
        self.pending = [c for c in waiting if c[0] >= self.accepted_end]
        retain = max(0, self.cursor - _RADIUS)
        if self.pending:
            retain = min(retain, self.pending[0][0])
        self.iq = self.iq[retain-self.base:]
        self.base = retain
        return packets


def measure(frames, seed, snr):
    """Blind PER using paired draws and energy of the entire on-air frame."""
    rng = np.random.default_rng(seed)
    sigma = np.sqrt(0.5 * 10**(-snr / 10))
    cases = [(4, 1.), (8, 1.), (8, np.sqrt(LENGTH[4] / LENGTH[8]))]
    errors = [0] * 3
    unexpected = [0] * 3
    for _ in range(frames):
        payload = rng.bytes(32)
        delay = int(rng.integers(0, 1024))
        cfo = rng.uniform(-5500., 5500.)
        phase = rng.uniform(-np.pi, np.pi)
        samples = delay + LENGTH[8]
        noise = sigma * (rng.normal(size=samples) + 1j*rng.normal(size=samples))
        rotation = np.exp(1j * (phase + 2*np.pi*cfo*np.arange(LENGTH[8])/FS))
        for j, (profile, amplitude) in enumerate(cases):
            tx = encode(payload, profile)
            stream = noise[:delay+len(tx)].copy()
            stream[delay:] += amplitude * tx * rotation[:len(tx)]
            packets = DualReceiver().feed(stream)
            matched = sum(p.profile == profile and p.payload == payload for p in packets)
            errors[j] += int(matched == 0)
            unexpected[j] += len(packets) - min(1, matched)
    return [dict(profile=k, power_scale=float(a*a), frames=frames, errors=e, per=e/frames, unexpected=u,
                 seed=seed, baseline_snr_db=snr) for (k,a),e,u in zip(cases, errors, unexpected)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest='command', required=True)
    enc = cmds.add_parser('encode')
    enc.add_argument('payload', type=Path)
    enc.add_argument('iq', type=Path)
    enc.add_argument('--profile', type=int, choices=(4,8), default=4)
    enc.add_argument('--force', action='store_true', help='atomically replace an existing distinct output')
    dec = cmds.add_parser('decode')
    dec.add_argument('iq', type=Path)
    per = cmds.add_parser('measure')
    per.add_argument('--frames', type=int, default=100)
    per.add_argument('--seed', type=int, default=2026)
    per.add_argument('--snr', type=float, default=-7.)
    args = parser.parse_args(argv)
    if args.command == 'encode':
        try:
            check_distinct(args.payload, args.iq)
            with atomic_writer(args.iq, args.force) as output:
                encode(args.payload.read_bytes(), args.profile).astype('<c8').tofile(output)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    elif args.command == 'decode':
        if args.iq.stat().st_size % 8:
            parser.error('partial complex float32 sample')
        rx = DualReceiver()
        with args.iq.open('rb') as source:
            while (data := source.read(4096 * 8)):
                for p in rx.feed(np.frombuffer(data, '<c8')):
                    print(json.dumps(dict(start=p.start, profile=p.profile, cfo_hz=p.cfo_hz, payload_hex=p.payload.hex())))
    else:
        if args.frames <= 0 or args.seed < 0 or not np.isfinite(args.snr):
            parser.error('require positive frames, nonnegative seed and finite SNR')
        print(json.dumps(measure(args.frames, args.seed, args.snr), indent=2))


if __name__ == '__main__':
    main()
