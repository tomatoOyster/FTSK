"""Record SoapySDR hardware-rate CF32 with metadata, then replay CFO search offline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
from radio import FS, TIMEOUT_US, checked, open_stream, rate_factor
from transfer import FileReceiver, parse_id
from io_helpers import atomic_writer, check_distinct


def write_capture(mod, dev, stream, chunk, args, dest):
    """Capture an exact sample count, reject overflow and timed discontinuities."""
    rate = args.rate
    goal = math.ceil(rate * args.seconds)
    last_time = None
    index_at_time = 0
    initial_time = None
    done = 0
    timeout_count = 0
    buffer = np.empty(chunk, np.complex64)
    digest = hashlib.sha256()
    start = time.monotonic()
    while done < goal:
        amount = min(chunk, goal-done)
        status = dev.readStream(stream, [buffer], amount, timeoutUs=TIMEOUT_US)
        count = status.ret
        if count in (0, mod.SOAPY_SDR_TIMEOUT):
            timeout_count += 1
            if time.monotonic() - start > args.seconds + 5:
                raise RuntimeError('capture timed out before receiving requested samples')
            continue
        checked(count, mod, 'capture readStream')
        if count > amount:
            raise RuntimeError('driver returned excessive sample count')
        if getattr(status, 'flags', 0) & getattr(mod, 'SOAPY_SDR_HAS_TIME', 0):
            now = status.timeNs
            if last_time is None:
                initial_time = now
            else:
                delta = (now-last_time)*rate/1e9 - (done-index_at_time)
                if abs(delta) > .51:
                    raise RuntimeError(f'hardware timestamp discontinuity of {delta:.2f} samples')
            last_time = now
            index_at_time = done
        raw = buffer[:count].astype('<c8', copy=False).tobytes()
        dest.write(raw)
        digest.update(raw)
        done += count
    return dict(samples=done, elapsed_s=time.monotonic()-start, timeout_count=timeout_count,
                sha256=digest.hexdigest(), first_hw_timestamp_ns=initial_time)


def replay_packets(path, rate, span, fast=False):
    from wide_rx import WideReceiver
    if fast:
        from fast_acquire import FastReceiver, FFTDecimator
        rx = WideReceiver(rate_factor(rate), span, receiver_factory=FastReceiver, decimator_factory=FFTDecimator)
    else:
        rx = WideReceiver(rate_factor(rate), span)
    if path.stat().st_size % 8:
        raise ValueError('truncated CF32 sample')
    with path.open('rb') as src:
        while raw := src.read(8*8192):
            for packet in rx.feed(np.frombuffer(raw, '<c8')):
                yield packet


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    cmds = p.add_subparsers(dest='cmd', required=True)
    record = cmds.add_parser('record', help='record hardware samples, create FILE.json sidecar')
    offline = cmds.add_parser('replay', help='search offsets on a hardware-rate recording')
    for cmd in (record, offline):
        cmd.add_argument('file', type=Path)
        cmd.add_argument('--rate', type=int, default=None, help='hardware sample rate in Hz')
    record.add_argument('--freq', type=float, required=True)
    record.add_argument('--seconds', type=float, required=True)
    record.add_argument('--device', default='')
    record.add_argument('--gain', type=float)
    record.add_argument('--channel', type=int, default=0)
    offline.add_argument('--fast', action='store_true', help='use differential capture prescreen and FFT decimation')
    offline.add_argument('--span', type=float, default=20000, help='CFO capture span +/- Hz')
    offline.add_argument('--output-file', type=Path, help='reassemble FT/1 frames and publish the verified file')
    offline.add_argument('--id', type=parse_id, help='optional FT/1 transfer id, 5 hex bytes')
    args = p.parse_args(argv)
    try:
        sidecar = Path(str(args.file)+'.json')
        if args.cmd == 'replay':
            meta = json.loads(sidecar.read_text('utf8')) if sidecar.exists() else {}
            if not isinstance(meta, dict):
                raise ValueError('capture metadata must be a JSON object')
            if 'rate_hz' in meta and (type(meta['rate_hz']) is not int or meta['rate_hz'] <= 0):
                raise ValueError('invalid capture metadata rate')
            if 'samples' in meta and (type(meta['samples']) is not int or meta['samples'] < 0):
                raise ValueError('invalid capture metadata sample count')
            rate = args.rate if args.rate is not None else meta.get('rate_hz')
            if rate is None:
                raise ValueError('supply --rate or provide a capture sidecar')
            if 'samples' in meta and args.file.stat().st_size != meta['samples']*8:
                raise ValueError('IQ length differs from capture metadata')
            if 'rate_hz' in meta and rate != meta['rate_hz']:
                raise ValueError('sample rate differs from capture metadata')
            if meta.get('sha256'):
                digest = hashlib.sha256()
                with args.file.open('rb') as f:
                    while block := f.read(1<<20):
                        digest.update(block)
                if digest.hexdigest() != meta['sha256']:
                    raise ValueError('IQ digest differs from capture metadata')
            packets = replay_packets(args.file, rate, args.span, fast=args.fast)
            if args.output_file is None:
                if args.id is not None:
                    raise ValueError('--id is used together with --output-file')
                for packet in packets:
                    print(json.dumps(dict(input_sample_estimate=packet.start*rate_factor(rate), baseband_start=packet.start,
                                          cfo_hz=packet.cfo_hz, payload_hex=packet.payload.hex())))
            else:
                check_distinct(args.file, args.output_file)
                check_distinct(sidecar, args.output_file)
                with FileReceiver(args.output_file, transfer_id=args.id) as target:
                    for packet in packets:
                        target.feed(packet.payload)
                    print(json.dumps(target.finish()))
        else:
            if args.file.exists() or sidecar.exists():
                raise FileExistsError('choose a new recording name')
            args.rate = args.rate if args.rate is not None else 1_024_000
            rate_factor(args.rate)
            if not math.isfinite(args.seconds) or args.seconds <= 0 or not math.isfinite(args.freq) or args.freq <= 0 or args.channel < 0:
                raise ValueError('invalid frequency, duration or channel')
            if args.gain is not None and not math.isfinite(args.gain):
                raise ValueError('gain must be finite')
            import SoapySDR as mod
            dev = mod.Device(args.device)
            start = datetime.now(timezone.utc).isoformat()
            with atomic_writer(args.file) as out, open_stream(mod, dev, mod.SOAPY_SDR_RX, args) as (stream, chunk):
                info = write_capture(mod, dev, stream, chunk, args, out)
            info.update(utc=start, format='little-endian interleaved float32 IQ', rate_hz=args.rate,
                        center_hz=args.freq, requested_gain_db=args.gain, device_selector=args.device, channel=args.channel)
            try:
                with atomic_writer(sidecar) as meta:
                    meta.write(json.dumps(info, indent=2).encode('utf8'))
            except OSError as exc:
                raise OSError(f'IQ saved at {args.file}; metadata not published. Use a clean sidecar path or replay the IQ with --rate {args.rate}: {exc}') from exc
            print(json.dumps(info))
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f'capture: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
