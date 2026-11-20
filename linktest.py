"""Count packet losses over an IQ file or SoapySDR bench link with known sent packet count."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import secrets
import struct
import sys
import time

import numpy as np
from frame import FS, encode_frame
from radio import acquisition_backend, open_stream, rate_factor, read_packets
from receiver import Receiver
from io_helpers import atomic_writer

TAG = b'FLT1'


def probe(run_id, index):
    head = TAG + run_id + struct.pack('>I', index)
    return head + hashlib.sha256(head).digest()[:16]


def confidence(errors, total):
    """95% Wilson score interval, assumes independent Bernoulli trials."""
    z = 1.95996398454
    p = errors / total
    denom = 1 + z*z/total
    center = (p+z*z/(2*total))/denom
    delta = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denom
    return [max(0, center-delta), min(1, center+delta)]


class Counts:
    def __init__(self, run_id, count):
        self.run_id, self.count = run_id, count
        self.seen = bytearray(count)
        self.duplicate = self.foreign = self.invalid = 0
        self.cfos = []

    def feed(self, packet):
        data = packet.payload
        if data[:12] != TAG + self.run_id:
            self.foreign += 1
            return
        index = int.from_bytes(data[12:16], 'big')
        if index >= self.count or data != probe(self.run_id, index):
            self.invalid += 1
            return
        if self.seen[index]:
            self.duplicate += 1
            return
        self.seen[index] = 1
        self.cfos.append(packet.cfo_hz)

    def summary(self):
        errors = self.count - len(self.cfos)
        return dict(run_id=self.run_id.hex(), expected=self.count, received_unique=len(self.cfos), missed=errors,
                    duplicate=self.duplicate, foreign=self.foreign, invalid=self.invalid, per=errors/self.count,
                    per_95pct_wilson=confidence(errors, self.count),
                    first_missing_indices=[i for i, ok in enumerate(self.seen) if not ok][:32],
                    cfo_median_hz=float(np.median(self.cfos)) if self.cfos else None)


def receive_file(path, counts):
    if path.stat().st_size % 8:
        raise ValueError('incomplete complex float32 sample')
    rx = Receiver()
    with path.open('rb') as src:
        while chunk := src.read(4096*8):
            for packet in rx.feed(np.frombuffer(chunk, '<c8')):
                counts.feed(packet)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['generate', 'analyze', 'tx', 'rx'])
    p.add_argument('--iq', type=Path, help='generate/analyze: baseband .cf32 file')
    p.add_argument('--count', type=int, required=True, help='number to transmit or expected transmitted packets')
    p.add_argument('--id', help='8-byte test session id, hexadecimal (required by analyze/rx)')
    p.add_argument('--freq', type=float, help='RF center Hz, required by tx/rx')
    p.add_argument('--device', default='')
    p.add_argument('--rate', type=int, default=1_024_000)
    p.add_argument('--channel', type=int, default=0)
    p.add_argument('--fast', action='store_true', help='RX differential capture prescreen and FFT decimation')
    p.add_argument('--offset-hz', type=float, default=0, help='RX calibrated offset before decimation')
    p.add_argument('--gain', type=float)
    p.add_argument('--seconds', type=float, help='receive time in seconds (required for rx)')
    args = p.parse_args(argv)
    try:
        if not 1 <= args.count <= 1_000_000:
            raise ValueError('count must be between 1 and 1000000')
        if args.id is None and args.mode in ('analyze', 'rx'):
            raise ValueError('receive modes require --id')
        run_id = secrets.token_bytes(8) if args.id is None else bytes.fromhex(args.id)
        if len(run_id) != 8:
            raise ValueError('--id must be 8 bytes')
        records = (probe(run_id, i) for i in range(args.count))
        result = dict(run_id=run_id.hex(), frames=args.count)
        if args.mode == 'generate':
            if args.iq is None:
                raise ValueError('supply --iq')
            with atomic_writer(args.iq) as out:
                for record in records:
                    out.write(encode_frame(record).astype('<c8').tobytes())
        elif args.mode == 'analyze':
            if args.iq is None:
                raise ValueError('supply --iq')
            counts = Counts(run_id, args.count)
            receive_file(args.iq, counts)
            result = counts.summary()
        else:
            if args.freq is None or not math.isfinite(args.freq) or args.freq <= 0 or args.channel < 0:
                raise ValueError('supply a positive frequency and nonnegative channel')
            if args.gain is not None and not math.isfinite(args.gain):
                raise ValueError('gain must be finite')
            if args.mode == 'rx' and (args.seconds is None or not math.isfinite(args.seconds) or args.seconds <= 0):
                raise ValueError('supply a positive --seconds listen duration')
            import SoapySDR as mod
            factor = rate_factor(args.rate)
            dev = mod.Device(args.device)
            direction = mod.SOAPY_SDR_TX if args.mode == 'tx' else mod.SOAPY_SDR_RX
            with open_stream(mod, dev, direction, args) as (stream, chunk):
                if args.mode == 'tx':
                    from radio_file import transmit_records
                    queued, n = transmit_records(mod, dev, stream, chunk, factor, records)
                    time.sleep(0.05+chunk/args.rate)
                    result.update(queued=queued, air_seconds=n/args.rate)
                else:
                    counts = Counts(run_id, args.count)
                    rx, converter = acquisition_backend(args.fast)
                    for packet in read_packets(mod, dev, stream, chunk, factor, args.seconds, rx, offset_hz=args.offset_hz, decimator_factory=converter):
                        counts.feed(packet)
                    result = counts.summary()
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        print(f'linktest: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
