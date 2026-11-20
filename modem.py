"""FTSK-4 packet modem. IQ files are raw little-endian complex float32."""
import argparse
import json
from pathlib import Path
import numpy as np
from frame import FS, PAYLOAD_BYTES, encode_frame
from receiver import Receiver
from io_helpers import atomic_writer, check_distinct


def report(packet):
    return dict(start_sample=int(packet.start), cfo_hz=round(float(packet.cfo_hz), 2), payload_hex=packet.payload.hex())


def simulation(seed, count, snr, cfo, ppm=0.0, echo=0.0):
    rng = np.random.default_rng(seed)
    rx = Receiver()
    good = wrong = 0
    first = None
    for _ in range(count):
        payload = rng.bytes(PAYLOAD_BYTES)
        frame = encode_frame(payload)
        if ppm:
            t = np.arange(int(np.ceil(len(frame) * (1 + ppm * 1e-6)))) / (1 + ppm * 1e-6)
            frame = np.interp(t, np.arange(len(frame)), frame, right=0)
        if echo:
            frame = np.convolve(frame, [1, 0, 0, echo * 1j])
        offset = int(rng.integers(0, 1400))
        shifted = frame * np.exp(1j * (rng.uniform(-np.pi, np.pi) + 2*np.pi*cfo*np.arange(len(frame))/FS))
        stream = np.pad(shifted, (offset, 512))
        std = np.sqrt(0.5 * 10**(-snr/10))  # unit input signal power; SNR per complex sample
        stream += std * (rng.normal(size=len(stream)) + 1j*rng.normal(size=len(stream)))
        packets = []
        for start in range(0, len(stream), 403):
            packets.extend(rx.feed(stream[start:start+403]))
        matches = [p for p in packets if p.payload == payload]
        good += bool(matches)
        wrong += len(packets) - bool(matches)
        if first is None and matches:
            first = report(matches[0])
    return dict(frames=count, received_correct=good, unexpected_packets=wrong, packet_error_rate=(count-good)/count,
                snr_db_per_sample=snr, injected_cfo_hz=cfo, clock_ppm=ppm, echo_amplitude=echo, first_packet=first)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest='cmd', required=True)
    cmd = cmds.add_parser('encode', help='encode exactly 32 bytes to a .cf32 file')
    cmd.add_argument('payload', type=Path)
    cmd.add_argument('--force', action='store_true', help='atomically replace existing IQ output')
    cmd.add_argument('iq', type=Path)
    cmd = cmds.add_parser('decode', help='blind-decode a .cf32 stream to JSON lines')
    cmd.add_argument('iq', type=Path)
    cmd.add_argument('--receiver', choices=['baseline', 'fast', 'tracked', 'equalized'], default='baseline')
    cmd = cmds.add_parser('demo', help='blind receive packets in simulated noise')
    cmd.add_argument('--frames', type=int, default=20)
    cmd.add_argument('--seed', type=int, default=2026)
    cmd.add_argument('--snr', type=float, default=-3, help='signal/noise power per complex sample, dB')
    cmd.add_argument('--cfo', type=float, default=4000, help='carrier offset, Hz')
    cmd.add_argument('--ppm', type=float, default=0, help='sample clock error (linear interpolation model)')
    cmd.add_argument('--echo', type=float, default=0, help='relative amplitude of three-sample delayed path')
    args = parser.parse_args()
    if args.cmd == 'encode':
        try:
            check_distinct(args.payload, args.iq)
            with args.payload.open('rb') as src:
                data = src.read(PAYLOAD_BYTES+1)
            if len(data) != PAYLOAD_BYTES:
                raise ValueError('payload must be exactly 32 bytes')
            with atomic_writer(args.iq, args.force) as out:
                out.write(encode_frame(data).astype('<c8').tobytes())
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    elif args.cmd == 'decode':
        if args.iq.stat().st_size % 8:
            parser.error('truncated complex float32 file')
        if args.receiver == 'fast':
            from fast_acquire import FastReceiver as Backend
        elif args.receiver == 'tracked':
            from tracking_rx import TrackingReceiver as Backend
        elif args.receiver == 'equalized':
            from equalizer import EqualizedReceiver as Backend
        else:
            Backend = Receiver
        rx = Backend()
        try:
            with args.iq.open('rb') as stream:
                while (buf := stream.read(4096 * 8)):
                    for pkt in rx.feed(np.frombuffer(buf, '<c8')):
                        print(json.dumps(report(pkt)))
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    else:
        if args.frames < 1 or args.seed < 0 or abs(args.ppm) > 1000 or not np.all(np.isfinite([args.snr, args.cfo, args.echo, args.ppm])):
            parser.error('require positive frames, nonnegative seed, finite settings and |ppm| <= 1000')
        print(json.dumps(simulation(args.seed, args.frames, args.snr, args.cfo, args.ppm, args.echo), indent=2))


if __name__ == '__main__':
    main()
