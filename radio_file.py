"""Stream a file over FTSK-4 using a SoapySDR device. See TRANSFER.md."""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

from profiles import encode, LENGTH
from frame import FS
from radio import acquisition_backend, interpolate, open_stream, rate_factor, read_packets, write_samples
from receiver import Receiver
from transfer import FileReceiver, iter_records


def transmit_records(module, dev, stream, chunk, factor, records, profile=4):
    """Use a single burst. Each guarded PHY record has its own FIR tails."""
    count = total = 0
    for record in records:
        samples = interpolate(np.pad(encode(record, profile), (64, 64)), factor)
        samples *= 0.8 / max(1., float(np.max(np.abs(samples))))
        write_samples(module, dev, stream, chunk, samples, final=False)
        count += 1
        total += len(samples)
    # A tail provides receiver settling samples and a final end-of-burst marker.
    tail = np.zeros(128*factor, dtype=np.complex64)
    write_samples(module, dev, stream, chunk, tail)
    return count, total+len(tail)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['tx', 'rx'])
    parser.add_argument('path', type=Path, help='source file (TX) or new destination (RX)')
    parser.add_argument('--device', default='')
    parser.add_argument('--freq', required=True, type=float, help='center frequency in Hz')
    parser.add_argument('--gain', type=float)
    parser.add_argument('--rate', type=int, default=1_024_000)
    parser.add_argument('--channel', type=int, default=0)
    parser.add_argument('--profile', choices=('4', '8', 'auto'), default='4', help='auto is RX only')
    parser.add_argument('--tracked', action='store_true', help='RX profile-4 drift and two-path tracking retry')
    parser.add_argument('--fast', action='store_true', help='RX differential capture prescreen and FFT decimation')
    parser.add_argument('--offset-hz', type=float, default=0, help='RX calibrated offset before decimation')
    parser.add_argument('--seconds', type=float, default=30, help='RX deadline in seconds')
    parser.add_argument('--id', help='5-byte transfer id, hex; RX locks to the first transfer by default')
    parser.add_argument('--force', action='store_true', help='allow atomic replacement of RX destination')
    args = parser.parse_args(argv)
    try:
        factor = rate_factor(args.rate)
        if not np.isfinite(args.freq) or args.freq <= 0 or args.channel < 0:
            raise ValueError('invalid center frequency or channel')
        if not np.isfinite(args.seconds) or args.seconds <= 0:
            raise ValueError('seconds must be positive and finite')
        if args.gain is not None and not np.isfinite(args.gain):
            raise ValueError('gain must be finite')
        tid = bytes.fromhex(args.id) if args.id is not None else None
        if tid is not None and len(tid) != 5:
            raise ValueError('transfer id must contain 5 bytes')
        if args.mode == 'tx' and not args.path.is_file():
            raise ValueError('input must be a regular file')
        if args.mode == 'rx' and args.path.exists() and not args.force:
            raise ValueError('destination exists; use --force for replacement')
        if args.mode == 'tx' and (args.profile == 'auto' or args.tracked or args.fast or args.offset_hz):
            raise ValueError('auto, --tracked, --fast and --offset-hz are RX only')
        if args.mode == 'rx':
            rx, converter = acquisition_backend(args.fast, args.profile, tracked=args.tracked, offset_hz=args.offset_hz)
        import SoapySDR as mod
        dev = mod.Device(args.device)
        if args.mode == 'tx':
            with open_stream(mod, dev, mod.SOAPY_SDR_TX, args) as (stream, chunk):
                count, total = transmit_records(mod, dev, stream, chunk, factor, iter_records(args.path, transfer_id=tid), int(args.profile))
                # Some drivers have no TX-complete event. Allow one full frame plus the FIFO time.
                time.sleep(max(0.05, (LENGTH[int(args.profile)]+192) / FS) + chunk / args.rate)
            print(json.dumps(dict(records_queued=count, samples_queued=total, air_seconds=total/args.rate)))
        else:
            with FileReceiver(args.path, transfer_id=tid, force=args.force) as file_rx:
                with open_stream(mod, dev, mod.SOAPY_SDR_RX, args) as (stream, chunk):
                    for packet in read_packets(mod, dev, stream, chunk, factor, args.seconds, rx, offset_hz=args.offset_hz, decimator_factory=converter):
                        file_rx.feed(packet.payload)
                        if file_rx.ready:
                            break
                print(json.dumps(file_rx.finish()))
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f'radio_file: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
