"""Optional single-channel SoapySDR adapter for the 128 kS/s FTSK-4 modem."""
import argparse
from contextlib import contextmanager
import sys
import time

import numpy as np

FS = 128_000
TIMEOUT_US = 100_000


def rate_factor(rate):
    if rate < FS or rate % FS:
        raise ValueError(f'device rate must be a positive integer multiple of {FS}')
    return rate // FS


def fir(factor):
    # +/-32 baseband samples, cutoff 64 kHz. Keep the 60 kHz edge tone.
    t = np.arange(-32 * factor, 32 * factor + 1) / factor
    h = np.sinc(t) * np.kaiser(len(t), 7.5)
    return (h / h.sum()).astype(np.float32)


def interpolate(iq, factor):
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    if factor == 1:
        return iq
    taps = fir(factor) * factor
    result = np.zeros((len(iq) + 64, factor), dtype=np.complex64)
    for phase in range(factor):
        samples = np.convolve(iq, taps[phase::factor])
        result[:len(samples), phase] = samples
    return result.ravel()


class Decimator:
    """Causal anti-alias filter with persistent delay line and sample phase."""

    def __init__(self, factor):
        self.factor = factor
        self.taps = fir(factor) if factor > 1 else np.ones(1, np.float32)
        self.tail = np.zeros(len(self.taps) - 1, np.complex64)
        self.phase = 0

    def feed(self, iq):
        if self.factor == 1 or not len(iq):
            return iq
        joined = np.concatenate((self.tail, iq))
        windows = np.lib.stride_tricks.sliding_window_view(joined, len(self.taps))
        output = windows[self.phase::self.factor] @ self.taps[::-1]
        self.phase = (self.phase - len(iq)) % self.factor
        self.tail = joined[-len(self.tail):].copy()
        return output


def checked(code, module, operation):
    if code is not None and code < 0:
        raise RuntimeError(f'{operation}: {module.errToStr(code)} ({code})')


@contextmanager
def open_stream(module, dev, direction, args):
    ch = args.channel
    dev.setSampleRate(direction, ch, args.rate)
    actual = dev.getSampleRate(direction, ch)
    if not np.isclose(actual, args.rate, atol=0.5, rtol=0):
        raise RuntimeError(f'device selected {actual} samples/s instead of {args.rate}')
    dev.setFrequency(direction, ch, args.freq)
    if args.gain is not None:
        dev.setGain(direction, ch, args.gain)
    stream = dev.setupStream(direction, module.SOAPY_SDR_CF32, [ch])
    try:
        mtu = dev.getStreamMTU(stream)
        if mtu <= 0:
            raise RuntimeError(f'invalid stream MTU {mtu}')
        checked(dev.activateStream(stream), module, 'activateStream')
        try:
            yield stream, min(8192, mtu)
        finally:
            checked(dev.deactivateStream(stream), module, 'deactivateStream')
    finally:
        dev.closeStream(stream)


def write_samples(module, dev, stream, chunk, samples, *, final=True):
    offset = stalls = 0
    while offset < len(samples):
        # Commit the end-of-burst flag only on the last sample. A short
        # write on an earlier fragment must not terminate the burst.
        remaining = len(samples) - offset
        count = min(chunk, max(1, remaining - int(final)))
        flags = module.SOAPY_SDR_END_BURST if final and remaining == 1 else 0
        block = samples[offset:offset + count]
        n = dev.writeStream(stream, [block], count, flags=flags, timeoutUs=TIMEOUT_US).ret
        if n in (0, module.SOAPY_SDR_TIMEOUT):
            stalls += 1
            if stalls >= 10:
                raise RuntimeError('TX stream stalled for 10 consecutive writes')
            continue
        checked(n, module, 'writeStream')
        if n > count:
            raise RuntimeError(f'invalid write count {n} for {count} samples')
        stalls = 0
        offset += n


def acquisition_backend(fast=False, profile='4', *, tracked=False, offset_hz=0.0):
    """Select a demodulator independently from calibrated RF-rate mixing."""
    profile = str(profile)
    if tracked and (fast or profile != '4'):
        raise ValueError('--tracked requires profile 4 and cannot be combined with --fast')
    if profile in ('8', 'auto'):
        if fast:
            raise ValueError('--fast acquisition supports profile 4 only')
        from profiles import DualReceiver
        return DualReceiver((8,) if profile == '8' else (4, 8)), Decimator
    if profile != '4':
        raise ValueError('RX profile must be 4, 8 or auto')
    if tracked:
        from tracking_rx import TrackingReceiver
        return TrackingReceiver(), Decimator
    from receiver import Receiver
    if fast:
        from fast_acquire import FastReceiver, FFTDecimator
        return FastReceiver(), FFTDecimator
    return Receiver(), Decimator


def read_packets(module, dev, stream, chunk, factor, seconds, receiver, *, offset_hz=0.0, decimator_factory=Decimator):
    """Mix by the calibrated offset before the low-pass filter, then decimate."""
    from dataclasses import replace
    if not np.isfinite(offset_hz) or abs(offset_hz)+60_000 > FS*factor/2:
        raise ValueError('requested calibration leaves outer tones outside device Nyquist')
    converter = decimator_factory(factor)
    buffer = np.empty(chunk, dtype=np.complex64)
    increment = offset_hz / (FS * factor)
    phase = 0.0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        n = dev.readStream(stream, [buffer], chunk, timeoutUs=TIMEOUT_US).ret
        if n in (0, module.SOAPY_SDR_TIMEOUT):
            continue
        checked(n, module, 'readStream')  # Overflow breaks the baseband timeline.
        if n > chunk:
            raise RuntimeError(f'invalid read count {n} for {chunk} samples')
        data = buffer[:n]
        if increment:
            data = data * np.exp(-2j*np.pi*(phase + increment*np.arange(n))).astype(np.complex64)
            phase = (phase + increment*n) % 1.0
        baseband = converter.feed(data)
        if len(baseband):
            for packet in receiver.feed(baseband.copy()):
                yield replace(packet, cfo_hz=packet.cfo_hz + offset_hz) if offset_hz else packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    for name in ('tx', 'rx'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--device', default='', help='Soapy device selector, e.g. driver=lime')
        cmd.add_argument('--freq', required=True, type=float, help='center frequency in Hz')
        cmd.add_argument('--rate', default=FS, type=int, help='device samples/s, multiple of 128000')
        cmd.add_argument('--gain', type=float, help='device gain in dB (default: device setting)')
        cmd.add_argument('--channel', type=int, default=0)
        cmd.add_argument('--profile', choices=('4', '8') if name == 'tx' else ('4', '8', 'auto'), default='4')
        if name == 'tx':
            cmd.add_argument('--payload-hex', required=True, help='32 bytes encoded in hex')
        else:
            cmd.add_argument('--tracked', action='store_true', help='profile-4 drift and two-path tracking retry')
            cmd.add_argument('--seconds', type=float, default=10.0, help='wall-clock listening duration')
            cmd.add_argument('--fast', action='store_true', help='use differential capture prescreen and FFT decimation')
            cmd.add_argument('--offset-hz', type=float, default=0, help='calibrated carrier offset, applied before decimation')
    args = parser.parse_args(argv)
    try:
        factor = rate_factor(args.rate)
        if not np.isfinite(args.freq) or args.freq <= 0 or args.channel < 0:
            raise ValueError('frequency must be finite and positive, channel nonnegative')
        if args.mode == 'tx':
            payload = bytes.fromhex(args.payload_hex)
            if len(payload) != 32:
                raise ValueError('payload must be exactly 32 bytes')
            from profiles import encode
            # Guard before/after packet allows filtering and receiver lookahead.
            samples = interpolate(np.pad(encode(payload, int(args.profile)), (256, 256)), factor)
            samples *= 0.8 / max(1., float(np.max(np.abs(samples))))
        else:
            if not np.isfinite(args.seconds) or args.seconds <= 0:
                raise ValueError('seconds must be finite and positive')
            receiver, converter = acquisition_backend(args.fast, args.profile, tracked=args.tracked, offset_hz=args.offset_hz)
        try:
            import SoapySDR as module
        except ImportError as exc:
            raise RuntimeError('install SoapySDR Python bindings and the driver for your device') from exc
        dev = module.Device(args.device)
        direction = module.SOAPY_SDR_TX if args.mode == 'tx' else module.SOAPY_SDR_RX
        with open_stream(module, dev, direction, args) as (stream, chunk):
            if args.mode == 'tx':
                write_samples(module, dev, stream, chunk, samples)
                # Drivers may acknowledge writes while IQ remains in their FIFO.
                time.sleep(len(samples) / args.rate + 0.02)
                print(f'Queued {len(payload)} payload bytes, {len(samples)} device samples')
            else:
                for pkt in read_packets(module, dev, stream, chunk, factor, args.seconds, receiver, offset_hz=args.offset_hz, decimator_factory=converter):
                    label = f'profile={pkt.profile} ' if hasattr(pkt, 'profile') else ''
                    print(f'{label}start={pkt.start} cfo_hz={pkt.cfo_hz:.2f} payload={pkt.payload.hex()}', flush=True)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f'radio: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
