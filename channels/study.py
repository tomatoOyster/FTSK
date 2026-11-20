"""Paired channel sensitivity trials using the released FTSK-4 receiver."""
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from frame import BOOK, FS, FRAME_SAMPLES, HEADER_TONES, pack_symbols
from receiver import Receiver

SEED = 194911
SAMPLES = 6144


@dataclass(frozen=True)
class Channel:
    phase: float = 0.0       # fractional onset delay in nominal baseband samples
    ppm: float = 0.0         # RX sample clock relative to nominal TX clock
    slope: float = 0.0       # CFO derivative, Hz/s
    delay_us: float = 0.0
    echo: float = 0.0        # delayed/direct amplitude ratio
    dc: float = 0.0          # DC magnitude relative to unit reference voltage
    gain_db: float = 0.0     # Q/I gain mismatch
    skew_deg: float = 0.0    # Q quadrature phase error


CASES = {'base': Channel(), 'phase25': Channel(phase=.25), 'phase50': Channel(phase=.5),
         'clock25': Channel(ppm=25), 'clock-50': Channel(ppm=-50), 'clock250': Channel(ppm=250),
         'clock1000': Channel(ppm=1000), 'clock3000': Channel(ppm=3000),
         'slope1000': Channel(slope=1000), 'slope25000': Channel(slope=25000),
         'slope50000': Channel(slope=50000), 'slope100000': Channel(slope=100000),
         'echo2': Channel(delay_us=2,echo=.4), 'echo20': Channel(delay_us=20,echo=.7),
         'echo80': Channel(delay_us=80,echo=.9), 'echo250': Channel(delay_us=250,echo=1),
         'dc': Channel(dc=.10), 'iq': Channel(gain_db=1,skew_deg=5),
         'dc_iq': Channel(dc=.10,gain_db=1,skew_deg=5),
         'combined': Channel(phase=.37,ppm=25,slope=1000,delay_us=2,echo=.4,dc=.10,gain_db=1,skew_deg=5)}


def noise_and_reference(index, seed, osr):
    rng = np.random.default_rng(np.random.SeedSequence([seed, index]))
    payload = rng.bytes(32)
    start = int(rng.integers(350, 1000))
    carrier_phase, echo_phase = rng.uniform(-np.pi, np.pi, 2)
    tones = np.r_[HEADER_TONES, BOOK[pack_symbols(payload)].ravel()] - 15
    x = np.arange(-24*osr, 24*osr+1)/osr
    h = np.sinc(x)*np.kaiser(len(x), 8)
    h /= h.sum()
    # Generate stationary noise across filter warmup; normalize by FIR L2 gain.
    count = SAMPLES*osr + len(h)-1
    white = (rng.normal(size=count)+1j*rng.normal(size=count))/np.sqrt(2*np.dot(h,h))
    noise = np.convolve(white, h, mode='valid')[::osr]
    return payload, start, carrier_phase, echo_phase, tones, h, noise


def waveform(t, tones):
    # Exact continuous-phase rectangular tones, evaluated on the RX grid.
    slot = np.floor(t/32).astype(int)
    output = np.zeros(len(t), complex)
    valid = (slot >= 0) & (slot < len(tones))
    output[valid] = np.exp(2j*np.pi*tones[slot[valid]]*(t[valid]%32)/32)
    return output


def capture(reference, channel, snr, osr):
    payload, start, carrier_phase, echo_phase, tones, h, noise = reference
    half = (len(h)-1)//2
    t = np.arange(-half, SAMPLES*osr+half)/osr/(1+channel.ppm*1e-6) - start-channel.phase
    direct = waveform(t, tones)
    if channel.echo:
        direct += channel.echo*np.exp(1j*echo_phase)*waveform(t-channel.delay_us*FS/1e6, tones)
        direct /= np.sqrt(1+channel.echo**2)
    sec = t/FS
    angle = carrier_phase + 2*np.pi*(1200*sec + .5*channel.slope*sec**2)
    signal = np.convolve(direct*np.exp(1j*angle), h, mode='valid')[::osr]
    z = signal + noise*10**(-snr/20)
    skew = np.deg2rad(channel.skew_deg)
    q = 10**(channel.gain_db/20)*(z.imag*np.cos(skew)+z.real*np.sin(skew))
    z = z.real + 1j*q + channel.dc*np.exp(.7j)
    return z.astype('<c8')


def evaluate(iq, payload):
    receiver = Receiver()
    pkts = []
    for pos in range(0, len(iq), 731):
        pkts.extend(receiver.feed(iq[pos:pos+731]))
    good = sum(p.payload == payload for p in pkts)
    return good == 1 and len(pkts) == 1, len(pkts)-min(1, good)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trials', type=int, default=64)
    p.add_argument('--seed', type=int, default=SEED)
    p.add_argument('--osr', type=int, choices=(4,8,16),default=8)
    p.add_argument('--snr', type=float, default=-3)
    p.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    p.add_argument('--summary', type=Path)
    p.add_argument('--replay', type=Path, help='write a single trial as CF32; requires exactly one case')
    p.add_argument('--index', type=int, default=0)
    args = p.parse_args()
    if args.trials < 1 or args.index < 0 or args.seed < 0 or not np.isfinite(args.snr):
        p.error('invalid seed, index, SNR or trial count')
    if args.replay:
        if len(args.cases) != 1:
            p.error('select exactly one replay case')
        ref = noise_and_reference(args.index, args.seed, args.osr)
        iq = capture(ref, CASES[args.cases[0]], args.snr, args.osr)
        ok, extra = evaluate(iq, ref[0])
        with args.replay.open('xb') as stream:
            iq.tofile(stream)
        print(json.dumps(dict(case=args.cases[0], index=args.index, correct=ok, extra=extra, payload=ref[0].hex(),
                              cf32_sha256=hashlib.sha256(iq.tobytes()).hexdigest())))
        return
    names = list(dict.fromkeys(['base']+args.cases))
    rows = {name: dict(config=asdict(CASES[name]), successes=0, lost_vs_base=0, gained_vs_base=0, unexpected=0,
                       first_paired_loss=None) for name in names}
    for idx in range(args.index, args.index+args.trials):
        ref = noise_and_reference(idx, args.seed, args.osr)
        for name in names:
            iq = capture(ref, CASES[name], args.snr, args.osr)
            good, extra = evaluate(iq, ref[0])
            if name == 'base':
                base = good
            row = rows[name]
            row['successes'] += good
            row['unexpected'] += extra
            row['lost_vs_base'] += base and not good
            row['gained_vs_base'] += not base and good
            if base and not good and row['first_paired_loss'] is None:
                row['first_paired_loss'] = idx
    result = dict(index=args.index, trials=args.trials, seed=args.seed, osr=args.osr, snr_db=args.snr,
                  numpy=np.__version__, receiver_sha256=hashlib.sha256((ROOT/'receiver.py').read_bytes()).hexdigest(), cases=rows)
    text = json.dumps(result, indent=2)
    if args.summary:
        args.summary.write_text(text+'\n',encoding='utf-8')
    for name,row in rows.items():
        print(name, row['successes'], row['lost_vs_base'], row['gained_vs_base'], row['first_paired_loss'])


if __name__ == '__main__':
    main()
