"""FTSK-4 complex baseband demo. Run python ftsk_demo.py --help."""
import argparse
from itertools import combinations

import numpy as np

FS = 128_000
N = 32
SYMBOLS = np.arange(512)
BOOK = (SYMBOLS[:, None] % 31 + SYMBOLS[:, None] // 31 * np.array([0, 1, 3, 7])) % 31


def transmit(symbols):
    # Each segment spans an integer number of cycles, so boundary phase is zero.
    bins = BOOK[symbols] - 15
    return np.exp(2j * np.pi * bins[..., None] * np.arange(N) / N)


def energies(iq):
    spectrum = np.fft.fftshift(np.fft.fft(iq, axis=-1), axes=-1)
    return abs(spectrum[..., 1:]) ** 2  # discard the -64 kHz bin


def soft_decode(power):
    scores = sum(power[:, segment, BOOK[:, segment]] for segment in range(4))
    return scores.argmax(axis=1)


def hard_decode(peaks):
    # Erasures use -1, which matches no entry in the codebook.
    votes = sum((peaks[:, i, None] == BOOK[:, i]).astype(np.uint8) for i in range(4))
    return votes.argmax(axis=1)


def verify():
    power = energies(transmit(SYMBOLS))
    np.testing.assert_array_equal(power[163].argmax(axis=1), [8, 13, 23, 12])
    np.testing.assert_array_equal(soft_decode(power), SYMBOLS)
    for i in range(4):
        for offset in range(1, 31):
            peaks = BOOK.copy()
            peaks[:, i] = (peaks[:, i] + offset) % 31
            np.testing.assert_array_equal(hard_decode(peaks), SYMBOLS)
    for pair in combinations(range(4), 2):
        peaks = BOOK.copy()
        peaks[:, pair] = -1
        np.testing.assert_array_equal(hard_decode(peaks), SYMBOLS)
    print('PASS: 512 IQ round trips, 61440 single errors, 3072 known erasures (pairs).')


def sweep():
    rng = np.random.default_rng(2026)
    print('Eb/N0 (dB)   soft errors / 4096   hard errors / 4096')
    for db in (0, 2, 4, 6, 8):
        soft_errors = hard_errors = 0
        # Unit-amplitude samples, 128 samples per 9 information bits.
        noise_std = np.sqrt(128 / (9 * 2 * 10 ** (db / 10)))
        for _ in range(16):
            symbols = rng.integers(512, size=256)
            iq = transmit(symbols)
            noise = rng.normal(size=iq.shape) + 1j * rng.normal(size=iq.shape)
            power = energies(iq + noise_std * noise)
            soft_errors += np.count_nonzero(soft_decode(power) != symbols)
            hard_errors += np.count_nonzero(hard_decode(power.argmax(axis=2)) != symbols)
        print(f'{db:10d}   {soft_errors:18d}   {hard_errors:18d}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', type=int, default=163, help='9-bit input (0..511)')
    args = parser.parse_args()
    if not 0 <= args.symbol < 512:
        parser.error('--symbol must be between 0 and 511')
    power = energies(transmit([args.symbol]))
    tones = power[0].argmax(axis=1)
    print(f'TX {args.symbol:09b} ({args.symbol}) | FFT peaks {tones.tolist()} | kHz {((tones-15)*FS/N/1000).tolist()} | RX {soft_decode(power)[0]}')
    verify()
    sweep()
